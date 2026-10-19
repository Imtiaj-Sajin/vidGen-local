"""Password gate in front of ComfyUI, for remote (phone) access through a tunnel.

Runs on 127.0.0.1:8190 and forwards everything (HTTP, video range requests, websockets)
to ComfyUI on 127.0.0.1:8188 once the visitor has logged in.
The password lives in remote/password.txt (created on first start, never committed).
"""
import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

import aiohttp
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.environ.get("LTX_UPSTREAM", "http://127.0.0.1:8188")
PORT = int(os.environ.get("LTX_PROXY_PORT", "8190"))
COOKIE = "ltx_auth"
MAX_AGE = 30 * 24 * 3600
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "origin", "referer"}


def _read_or_create(name, factory):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(factory())
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


PASSWORD = _read_or_create("password.txt", lambda: secrets.token_urlsafe(9))
SECRET = _read_or_create("secret.txt", lambda: secrets.token_hex(32)).encode()
# token changes whenever the password changes, so old cookies stop working
TOKEN = hmac.new(SECRET, PASSWORD.encode(), hashlib.sha256).hexdigest()
_fails = defaultdict(deque)

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>LTX Lab login</title>
<style>
:root{color-scheme:light;--bg:#f4f4f2;--card:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#dcdbd5;--accent:#2a78d6;--bad:#c62f2e}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111110;--card:#1a1a19;--ink:#fff;--muted:#c3c2b7;--line:#34332f;--accent:#3987e5;--bad:#e66767}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,sans-serif;padding:16px;box-sizing:border-box}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;width:100%;max-width:340px}
h1{font-size:20px;margin:0 0 4px}p{color:var(--muted);margin:0 0 16px;font-size:14px}
input{width:100%;box-sizing:border-box;padding:12px;font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{margin-top:12px;width:100%;padding:12px;font:inherit;font-weight:600;border:0;border-radius:8px;background:var(--accent);color:#fff}
.err{color:var(--bad);font-size:14px;margin-top:10px}
</style></head><body><form method="post" action="/__login">
<h1>LTX Lab</h1><p>Enter the password to continue.</p>
<input type="password" name="password" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="__NEXT__"><button>Log in</button>__ERR__</form></body></html>"""


def _client_ip(request):
    return request.headers.get("CF-Connecting-IP") or request.remote or "?"


def _authed(request):
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), TOKEN)


def _login_page(next_url, err=""):
    safe_next = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/ltxlab"
    body = LOGIN_PAGE.replace("__NEXT__", safe_next.replace('"', "")).replace(
        "__ERR__", f'<div class="err">{err}</div>' if err else "")
    return web.Response(text=body, content_type="text/html", status=401 if err else 200)


async def login(request):
    ip = _client_ip(request)
    q = _fails[ip]
    while q and q[0] < time.time() - 600:
        q.popleft()
    form = await request.post()
    nxt = str(form.get("next", "/ltxlab"))
    if len(q) >= 10:
        return _login_page(nxt, "Too many attempts. Try again in 10 minutes.")
    if not hmac.compare_digest(str(form.get("password", "")), PASSWORD):
        q.append(time.time())
        await asyncio.sleep(1)
        return _login_page(nxt, "Wrong password.")
    q.clear()
    resp = web.HTTPFound(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/ltxlab")
    resp.set_cookie(COOKIE, TOKEN, max_age=MAX_AGE, httponly=True, secure=True, samesite="Lax")
    raise resp


async def logout(request):
    resp = web.HTTPFound("/__login")
    resp.del_cookie(COOKIE)
    raise resp


async def proxy(request):
    if request.path == "/__login":
        return _login_page(request.query.get("next", "/ltxlab"))
    if not _authed(request):
        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            raise web.HTTPFound("/__login?next=" + request.path_qs.replace("&", "%26"))
        return web.Response(status=401, text="login required")
    if request.path == "/":
        # phone users want the lab; desktop ComfyUI stays at /comfy
        if "comfy" not in request.query:
            raise web.HTTPFound("/ltxlab")
    session = request.app["session"]
    url = UPSTREAM + request.path_qs
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "cookie"}

    if request.headers.get("Upgrade", "").lower() == "websocket":
        ws_client = web.WebSocketResponse(autoping=True, max_msg_size=0)
        await ws_client.prepare(request)
        async with session.ws_connect(url.replace("http", "ws", 1), max_msg_size=0) as ws_up:
            async def pump(src, dst):
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:
                        break
            tasks = [asyncio.create_task(pump(ws_client, ws_up)), asyncio.create_task(pump(ws_up, ws_client))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
        return ws_client

    body = request.content if request.body_exists else None
    async with session.request(request.method, url, headers=headers, data=body,
                               allow_redirects=False, auto_decompress=False) as up:
        resp = web.StreamResponse(status=up.status, reason=up.reason)
        for k, v in up.headers.items():
            if k.lower() not in HOP:
                resp.headers.add(k, v)
        if "Content-Length" in up.headers and "Content-Encoding" not in up.headers:
            resp.content_length = int(up.headers["Content-Length"])
        await resp.prepare(request)
        async for chunk in up.content.iter_chunked(256 * 1024):
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def on_startup(app):
    app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10))


async def on_cleanup(app):
    await app["session"].close()


def main():
    app = web.Application(client_max_size=1024 ** 3)
    app.router.add_post("/__login", login)
    app.router.add_get("/__logout", logout)
    app.router.add_route("*", "/{tail:.*}", proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    print(f"[proxy] password gate on http://127.0.0.1:{PORT} -> {UPSTREAM}")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()
