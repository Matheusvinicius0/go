import uvloop
import asyncio
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
import httpx
import uvicorn
from urllib.parse import urljoin, quote
from contextlib import asynccontextmanager

VERSION = "2.0.0"

_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    # Cliente HTTP otimizado para streaming contínuo
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=30, keepalive_expiry=30),
        verify=False,
    )
    yield
    await _http_client.aclose()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return Response(content="<h1>FenixFlix Proxy Rodando!</h1><p>Adicione o manifest.json no Stremio.</p>", media_type="text/html")

@app.get("/manifest.json")
async def manifest_endpoint():
    return JSONResponse(content={
        "id": "com.fenixflix.live",
        "version": VERSION,
        "name": "FENIXFLIX TV",
        "description": "Seu Canal Ao Vivo (Bypass Proxy)",
        "logo": "https://i.imgur.com/9SKgxfU.png",
        "resources": ["stream", "catalog"],
        "types": ["tv"],
        "catalogs": [
            {"type": "tv", "id": "live_tv", "name": "TV Ao Vivo"}
        ],
        "idPrefixes": ["fenix"]
    })

@app.get("/catalog/{type}/{id}.json")
@app.get("/catalog/{type}/{id}/{extra}.json")
async def catalog_endpoint(type: str, id: str, extra: str = None, skip: int = 0):
    if type == "tv" and id == "live_tv":
        return JSONResponse(content={"metas": [
            {
                "id": "fenix_live_main",
                "type": "tv",
                "name": "Canal Fenix Ao Vivo",
                "description": "Link M3U8 fixo rodando pelo proxy",
                "poster": "https://i.imgur.com/9SKgxfU.png",
                "background": "https://dl.strem.io/addon-background.jpg"
            }
        ]})
    return JSONResponse(content={"metas": []})

@app.get("/stream/{type}/{id}.json")
async def stream(type: str, id: str, request: Request):
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
    return JSONResponse(content={"streams": []})

# =========================================================================
# LÓGICA DO PROXY + ROTA DO LINK FIXO
# =========================================================================

@app.get("/live")
async def live_fixo(request: Request):
    """Rota direta para o seu link fixo M3U8"""
    url_fixa = "http://67.220.74.155/live/Marcelo123/Marcelo321/116569.m3u8"
    host_atual = request.headers.get("host")
    proxy_url = f"http://{host_atual}/proxy?url={quote(url_fixa)}"
    return RedirectResponse(url=proxy_url)

@app.get("/proxy")
async def proxy_handler(request: Request, url: str):
    """Bypass de bloqueios de Referer e User-Agent"""
    if not url:
        return Response("Faltou o parâmetro 'url'", status_code=400)

    # Injeção de headers antibloqueio
    headers = {
        "Referer": "https://7embeddecanais.xyz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        req = _http_client.build_request("GET", url, headers=headers)
        r = await _http_client.send(req, stream=True)

        content_type = r.headers.get("Content-Type", "")

        # Se for M3U8, reescreve os links internos para passarem pelo proxy
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
                    absolute_url = urljoin(url, line)
                    proxied_url = f"http://{proxy_host}/proxy?url={quote(absolute_url)}"
                    rewritten_lines.append(proxied_url)

            return Response(
                content="\n".join(rewritten_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        else:
            # Se for os arquivos de vídeo (.ts), entrega em buffer para não travar a memória
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