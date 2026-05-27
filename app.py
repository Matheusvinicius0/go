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

VERSION = "2.0.1"

_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    # Cliente otimizado para streaming
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=30),
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
    return {"message": "FenixFlix TV Proxy Online", "version": VERSION}

@app.get("/manifest.json")
async def manifest_endpoint():
    return JSONResponse(content={
        "id": "com.fenixflix.live",
        "version": VERSION,
        "name": "FENIXFLIX TV",
        "description": "Proxy de TV Ao Vivo",
        "logo": "https://i.imgur.com/9SKgxfU.png",
        "resources": ["stream", "catalog"],
        "types": ["tv"],
        "catalogs": [{"type": "tv", "id": "live_tv", "name": "TV Ao Vivo"}],
        "idPrefixes": ["fenix"]
    })

@app.get("/catalog/{type}/{id}.json")
async def catalog_endpoint(type: str, id: str):
    if type == "tv" and id == "live_tv":
        return JSONResponse(content={"metas": [
            {
                "id": "fenix_live_main",
                "type": "tv",
                "name": "Canal Fenix Ao Vivo",
                "poster": "https://i.imgur.com/9SKgxfU.png"
            }
        ]})
    return JSONResponse(content={"metas": []})

@app.get("/stream/{type}/{id}.json")
async def stream(type: str, id: str, request: Request):
    # Aceita qualquer ID que contenha 'fenix' ou seja do tipo tv
    if "fenix" in id or type == "tv":
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

@app.get("/live")
async def live_fixo(request: Request):
    url_fixa = "http://67.220.74.155/live/Marcelo123/Marcelo321/116569.m3u8"
    host_atual = request.headers.get("host")
    proxy_url = f"http://{host_atual}/proxy?url={quote(url_fixa)}"
    return RedirectResponse(url=proxy_url)

@app.get("/proxy")
async def proxy_handler(request: Request, url: str):
    headers = {
        "Referer": "https://7embeddecanais.xyz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        req = _http_client.build_request("GET", url, headers=headers)
        r = await _http_client.send(req, stream=True)
        
        if "mpegurl" in r.headers.get("Content-Type", "").lower() or url.endswith(".m3u8"):
            await r.aread()
            text = r.text
            proxy_host = request.headers.get("host")
            rewritten = []
            for line in text.splitlines():
                if not line.strip() or line.startswith("#"):
                    rewritten.append(line)
                else:
                    abs_url = urljoin(url, line)
                    rewritten.append(f"http://{proxy_host}/proxy?url={quote(abs_url)}")
            return Response(content="\n".join(rewritten), media_type="application/vnd.apple.mpegurl")
        
        return StreamingResponse(r.aiter_bytes(), status_code=r.status_code, media_type=r.headers.get("Content-Type"), background=BackgroundTask(r.aclose))
    except Exception as e:
        return Response(str(e), status_code=502)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
