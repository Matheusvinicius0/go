import uvloop
import asyncio
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn
from urllib.parse import urljoin, quote
from contextlib import asynccontextmanager

_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=10.0, follow_redirects=True, verify=False)
    yield
    await _http_client.aclose()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/manifest.json")
async def manifest():
    return {
        "id": "com.globo.tv",
        "version": "1.0.0",
        "name": "GLOBO TV",
        "description": "Canal Ao Vivo",
        "types": ["tv"],
        "catalogs": [{"type": "tv", "id": "globo_catalog", "name": "GLOBO"}],
        "resources": ["stream", "catalog"]
    }

@app.get("/catalog/tv/globo_catalog.json")
async def catalog():
    return {"metas": [
        {
            "id": "globo_ao_vivo",
            "type": "tv",
            "name": "GLOBO",
            "poster": "https://i.imgur.com/9SKgxfU.png"
        }
    ]}

@app.get("/stream/tv/{id}.json")
async def stream(id: str, request: Request):
    if id == "globo_ao_vivo":
        host = request.headers.get("host")
        return {"streams": [{"name": "Globo", "url": f"http://{host}/live"}]}
    return {"streams": []}

@app.get("/live")
async def live(request: Request):
    url_m3u8 = "http://67.220.74.155/live/Marcelo123/Marcelo321/116569.m3u8"
    host = request.headers.get("host")
    return RedirectResponse(url=f"http://{host}/proxy?url={quote(url_m3u8)}")

@app.get("/proxy")
async def proxy(url: str, request: Request):
    try:
        r = await _http_client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if ".m3u8" in url:
            proxy_host = request.headers.get("host")
            text = r.text
            new_text = ""
            for line in text.splitlines():
                if line.startswith("http"):
                    new_text += f"http://{proxy_host}/proxy?url={quote(line)}\n"
                else:
                    new_text += line + "\n"
            return Response(new_text, media_type="application/vnd.apple.mpegurl")
        return Response(r.content, media_type=r.headers.get("Content-Type"))
    except:
        return Response("Erro no proxy", status_code=502)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
