import uvloop
import asyncio
# Troca o motor do asyncio antes de qualquer outra coisa
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, Response, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.background import BackgroundTask
import httpx
import os
import orjson
import uvicorn
import aiofiles
import re
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from urllib.parse import urljoin, quote

import on  # <-- Deixamos APENAS o ON

load_dotenv()

VERSION = "1.0.6"
CACHE_DIR = "cache"
SCRAPER_STATUS_FILE = os.path.join(CACHE_DIR, "scrapers_status.json")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

_http_client: httpx.AsyncClient = None
tmdb_semaphore = asyncio.Semaphore(7)

# --- SISTEMA DE CACHE GLOBAL ---
GLOBAL_SCRAPER_CACHE = None
CACHE_LOCK = asyncio.Lock()
_CACHE_DIRTY = False

def load_scraper_cache():
    global GLOBAL_SCRAPER_CACHE
    if GLOBAL_SCRAPER_CACHE is not None:
        return GLOBAL_SCRAPER_CACHE
    if os.path.exists(SCRAPER_STATUS_FILE):
        try:
            with open(SCRAPER_STATUS_FILE, "rb") as f:
                GLOBAL_SCRAPER_CACHE = orjson.loads(f.read())
                return GLOBAL_SCRAPER_CACHE
        except Exception as e:
            print(f"[CACHE ERROR] Falha ao ler scrapers_status.json: {e}")
    GLOBAL_SCRAPER_CACHE = {}
    return GLOBAL_SCRAPER_CACHE

async def save_scraper_cache(cache_data):
    global GLOBAL_SCRAPER_CACHE, _CACHE_DIRTY
    GLOBAL_SCRAPER_CACHE = cache_data
    _CACHE_DIRTY = True

async def background_cache_writer():
    global _CACHE_DIRTY, GLOBAL_SCRAPER_CACHE
    while True:
        await asyncio.sleep(10)
        if _CACHE_DIRTY and GLOBAL_SCRAPER_CACHE is not None:
            async with CACHE_LOCK:
                try:
                    async with aiofiles.open(SCRAPER_STATUS_FILE, mode="wb") as f:
                        await f.write(orjson.dumps(GLOBAL_SCRAPER_CACHE, option=orjson.OPT_INDENT_2))
                    _CACHE_DIRTY = False
                    print("[CACHE] Arquivo JSON sincronizado com sucesso no disco.")
                except Exception as e:
                    print(f"[CACHE FATAL] Erro na gravação: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=30, keepalive_expiry=30),
        verify=False,
    )

    task_writer = asyncio.create_task(background_cache_writer())

    yield

    await _http_client.aclose()
    task_writer.cancel()
    if _CACHE_DIRTY and GLOBAL_SCRAPER_CACHE is not None:
        with open(SCRAPER_STATUS_FILE, "wb") as f:
            f.write(orjson.dumps(GLOBAL_SCRAPER_CACHE, option=orjson.OPT_INDENT_2))

app = FastAPI(lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

async def obter_dados_base_tmdb(imdb_id: str, content_type: str, client: httpx.AsyncClient = None):
    tmdb_id_final = None
    real_imdb_id  = None
    titulos = []
    tmdb_type = "movie" if content_type == "movie" else "tv"

    async def _do_requests(c):
        nonlocal tmdb_id_final, real_imdb_id
        if imdb_id.startswith("tmdb:"):
            tmdb_id_final = imdb_id.split(":")[1]
            url_pt = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id_final}?api_key={TMDB_API_KEY}&language=pt-BR&append_to_response=external_ids"
            url_en = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id_final}?api_key={TMDB_API_KEY}&language=en-US&append_to_response=external_ids"
            reqs = await asyncio.gather(c.get(url_pt), c.get(url_en), return_exceptions=True)
            for r in reqs:
                if not isinstance(r, Exception) and r.status_code == 200:
                    data = r.json()
                    if not real_imdb_id:
                        real_imdb_id = (data.get("external_ids") or {}).get("imdb_id") or None
                    name = data.get("title") or data.get("name")
                    if name and name not in titulos:
                        titulos.append(name)
        else:
            real_imdb_id = imdb_id
            url_pt = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_API_KEY}&external_source=imdb_id&language=pt-BR"
            url_en = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_API_KEY}&external_source=imdb_id&language=en-US"
            reqs = await asyncio.gather(c.get(url_pt), c.get(url_en), return_exceptions=True)
            for r in reqs:
                if not isinstance(r, Exception) and r.status_code == 200:
                    data = r.json()
                    results = data.get(f"{tmdb_type}_results", [])
                    if results:
                        if not tmdb_id_final:
                            tmdb_id_final = str(results[0].get("id"))
                        name = results[0].get("title") or results[0].get("name")
                        if name and name not in titulos:
                            titulos.append(name)

    try:
        await _do_requests(client or _http_client)
    except:
        pass

    return tmdb_id_final, real_imdb_id, titulos

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    manifest_data = {"name": "FENIXFLIX", "description": "Addon de Filmes, Séries e TV", "types": ["movie", "series", "tv"]}
    return templates.TemplateResponse(request=request, name="index.html", context={"manifest": manifest_data, "version": VERSION})

@app.get("/manifest.json")
async def manifest_endpoint():
    return JSONResponse(content={
        "id": "com.fenixflix", "version": VERSION, "name": "FENIXFLIX",
        "description": "Addon de Filmes, Séries e Canais",
        "logo": "https://i.imgur.com/9SKgxfU.png",
        "background": "https://dl.strem.io/addon-background.jpg",
        "resources": ["stream", "catalog"],
        "types": ["movie", "series", "tv"],
        "catalogs": [
            # Catálogo único criado exclusivamente para a sua Live (o Main)
            {"type": "tv", "id": "live_tv", "name": "TV Ao Vivo"}
        ],
        "idPrefixes": ["tt", "tmdb", "fenix"]
    })

@app.get("/catalog/{type}/{id}.json")
@app.get("/catalog/{type}/{id}/{extra}.json")
async def catalog_endpoint(type: str, id: str, extra: str = None, skip: int = 0):
    if (extra and "skip=" in extra) or skip > 0:
        return JSONResponse(content={"metas": []})

    # Aqui alimentamos o catálogo novo
    if type == "tv" and id == "live_tv":
        return JSONResponse(content={"metas": [
            {
                "id": "fenix_live_main",
                "type": "tv",
                "name": "Canal Fenix Ao Vivo",
                "description": "Seu link fixo passando pelo proxy.",
                "poster": "https://i.imgur.com/9SKgxfU.png",
                "background": "https://dl.strem.io/addon-background.jpg"
            }
        ]})

    return JSONResponse(content={"metas": []})

@app.get("/stream/{type}/{id}.json")
@limiter.limit("30/minute")
async def stream(type: str, id: str, request: Request):
    # Verifica se é o nosso canal de TV customizado
    if type == "tv" and id == "fenix_live_main":
        host_atual = request.headers.get("host")
        return JSONResponse(content={"streams": [
            {
                "name": "FenixFlix",
                "title": "Assistir Ao Vivo",
                "url": f"http://{host_atual}/live",
                "behaviorHints": {"notWebReady": False}
            }
        ]})

    season, episode = None, None
    if id.startswith("tmdb:"):
        parts = id.split(':')
        clean_id = f"tmdb:{parts[1]}"
        if type == 'series' and len(parts) >= 4:
            season, episode = int(parts[2]), int(parts[3])
    else:
        parts = id.split(':')
        clean_id = parts[0]
        if type == 'series' and len(parts) >= 3:
            season, episode = int(parts[1]), int(parts[2])

    cache_status = load_scraper_cache()
    entry = cache_status.get(clean_id)
    base_id = clean_id

    if entry is None and clean_id.startswith("tmdb:"):
        tmdb_id_raw = clean_id.split(":")[1]
        for key, val in cache_status.items():
            if val.get("tmdb_id") == tmdb_id_raw:
                entry = val
                base_id = key
                break

    if entry and entry.get("tmdb_id"):
        tmdb_id = entry.get("tmdb_id")
        titles  = entry.get("titles", [])

        if type == "series":
            scraper_flags = entry.get("episodes", {}).get(f"{season}:{episode}", {})
            if isinstance(scraper_flags, dict) and "flags" in scraper_flags:
                scraper_flags = scraper_flags["flags"]
        else:
            scraper_flags = entry.get("scrapers", {})
    else:
        tmdb_id, real_imdb_id, titles = await obter_dados_base_tmdb(clean_id, type, client=_http_client)
        scraper_flags = {}
        if real_imdb_id and real_imdb_id.startswith("tt"):
            base_id = real_imdb_id
        else:
            base_id = clean_id

    outras_tarefas = {}
    novos_flags = scraper_flags.copy()

    # Só roda o ON (Serve, Streamflix e MyWallpaper foram deletados)
    if tmdb_id:
        on_flag = scraper_flags.get("on")
        azullog_falhou_total = isinstance(on_flag, dict) and on_flag.get("D") == "N" and on_flag.get("L") == "N"

        if on_flag == "N" or azullog_falhou_total:
            novos_flags["on"] = on_flag
        else:
            on_cache = {}
            if isinstance(on_flag, dict):
                for k, v in on_flag.items():
                    if isinstance(v, str) and not v.startswith("http") and v != "N" and v != "S":
                        on_cache[k] = f"https://www.mediafire.com/file_premium/{v}/file"
                    else:
                        on_cache[k] = v

            outras_tarefas["on"] = asyncio.create_task(on.search_serve(tmdb_id, type, season, episode, client=_http_client, cached_links=on_cache))

    tarefas_ativas = outras_tarefas

    if tarefas_ativas:
        done, pending = await asyncio.wait(tarefas_ativas.values(), timeout=32.0)
        for p in pending:
            p.cancel()
    else:
        pending = set()

    todos_streams = []

    if "on" in tarefas_ativas and tarefas_ativas["on"] not in pending:
        try:
            res = tarefas_ativas["on"].result()
            if res:
                on_dict = {}
                for s in res:
                    if isinstance(s, dict) and s.get("_cache_key"):
                        url_completa = s.get("_mediafire_url", "N")
                        if url_completa == "N":
                            on_dict[s["_cache_key"]] = "N"
                        else:
                            match = re.search(r'mediafire\.com/(?:file_premium|file)/([a-zA-Z0-9]+)', url_completa)
                            on_dict[s["_cache_key"]] = match.group(1) if match else url_completa

                novos_flags["on"] = on_dict if on_dict else "S"

                for s_info in res:
                    if isinstance(s_info, dict) and s_info.get("url"):
                        s_info.pop("_slug_found", None)
                        s_info.pop("_mediafire_url", None)
                        s_info.pop("_label", None)
                        s_info.pop("_cache_key", None)
                        if "behaviorHints" not in s_info:
                            s_info["behaviorHints"] = {"notWebReady": False, "bingeGroup": "fenixflix"}
                        todos_streams.append(s_info)
            else:
                novos_flags["on"] = "N"
        except Exception as e:
            novos_flags["on"] = "N"

    cache_mudou = False

    if base_id not in cache_status:
        cache_status[base_id] = {"tmdb_id": tmdb_id, "titles": titles, "type": type}
        cache_mudou = True

    episodes_backup = cache_status[base_id].pop("episodes", None)
    scrapers_backup = cache_status[base_id].pop("scrapers", None)

    if tmdb_id and type == "series":
        if episodes_backup is None:
            episodes_backup = {}
        flags_para_salvar = {k: v for k, v in novos_flags.items() if k != "doramogo_slug"}

        ep_key = f"{season}:{episode}"
        if ep_key not in episodes_backup:
            episodes_backup[ep_key] = flags_para_salvar
            cache_mudou = True
        else:
            if episodes_backup[ep_key] != flags_para_salvar:
                episodes_backup[ep_key].update(flags_para_salvar)
                cache_mudou = True

        cache_status[base_id]["episodes"] = episodes_backup

    elif tmdb_id:
        if scrapers_backup is None:
            scrapers_backup = {}
        if scrapers_backup != novos_flags:
            scrapers_backup.update(novos_flags)
            cache_mudou = True
        cache_status[base_id]["scrapers"] = scrapers_backup

    if "streams_data" in cache_status[base_id]:
        del cache_status[base_id]["streams_data"]
        cache_mudou = True

    if cache_mudou:
        await save_scraper_cache(cache_status)

    return JSONResponse(content={"streams": todos_streams})


# =========================================================================
# LÓGICA DO PROXY + ROTA DO LINK FIXO (TV AO VIVO)
# =========================================================================

@app.get("/live")
async def live_fixo(request: Request):
    """Rota direta para o seu link fixo M3U8"""
    url_fixa = "http://67.220.74.155/live/Marcelo123/Marcelo321/116569.m3u8"
    host_atual = request.headers.get("host")
    proxy_url = f"http://{host_atual}/proxy?url={quote(url_fixa)}"

    # Redireciona para o Proxy processar com os headers corretos
    return RedirectResponse(url=proxy_url)

@app.get("/proxy")
async def proxy_handler(request: Request, url: str):
    """Bypass de bloqueios de Referer e User-Agent"""
    if not url:
        return Response("Faltou o parâmetro 'url'", status_code=400)

    # Injeção de headers (O Segredo para burlar o bloqueio)
    headers = {
        "Referer": "https://7embeddecanais.xyz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        req = _http_client.build_request("GET", url, headers=headers)
        r = await _http_client.send(req, stream=True)

        content_type = r.headers.get("Content-Type", "")

        # Verifica se é um arquivo de texto M3U8
        if "mpegurl" in content_type.lower() or url.endswith(".m3u8"):
            await r.aread()
            text = r.text
            rewritten_lines = []
            proxy_host = request.headers.get("host")

            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    rewritten_lines.append(line)
                else:
                    # Resolve links relativos e reescreve passando pelo proxy local
                    absolute_url = urljoin(url, line)
                    proxied_url = f"http://{proxy_host}/proxy?url={quote(absolute_url)}"
                    rewritten_lines.append(proxied_url)

            return Response(
                content="\n".join(rewritten_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        else:
            # Se for vídeo direto (.ts), faz o bypass em tempo real
            async def stream_generator():
                async for chunk in r.aiter_bytes():
                    yield chunk

            return StreamingResponse(
                stream_generator(),
                status_code=r.status_code,
                media_type=content_type,
                background=BackgroundTask(r.aclose)
            )

    except Exception as e:
        return Response(f"Erro ao conectar no servidor original: {str(e)}", status_code=502)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
