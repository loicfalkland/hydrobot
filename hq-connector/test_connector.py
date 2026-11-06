"""Tests for hq_mcp_server.

Default (fully mocked, no browser, CI-safe):
    python test_connector.py

Webview mode: opens a real embedded webview (pywebview; falls back to the system
browser) on a local mock identity provider with a username/password form. You sign
in inside the webview, then the test runs the API calls (mocked backend) and logout:
    pip install pywebview        # optional
    python test_connector.py --webview

No real credentials or external network are used in either mode; tokens never
appear in output.
"""
import argparse
import asyncio
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("HQ_ISSUER", "https://connexion-nonprod.example.com/tfp/tenant/b2c_1a_test/v2.0")
os.environ.setdefault("HQ_CLIENT_ID", "test-client-id")

import httpx
import hq_mcp_server as srv

MOCK_IDP_PORT = 8975
PRIVATE_PATH = "/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte"

LOGIN_PAGE = """<html><body style="font-family:sans-serif">
<h2>Mock Hydro-Quebec sign-in</h2>
<form onsubmit="return false">
<input id="u" placeholder="username"><br><br>
<input id="p" type="password" placeholder="password"><br><br>
<button onclick="var u=document.getElementById('u').value,p=document.getElementById('p').value;
if(!u||!p){alert('enter credentials');return;}
location.href='{redirect}?code=mock-auth-code&state={state}&login='+encodeURIComponent(u);">Sign in</button>
</form></body></html>"""


def _install_token_mock():
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={
                "access_token": "AT-SECRET", "refresh_token": "RT-SECRET",
                "token_type": "Bearer", "expires_in": 3600, "scope": "openid offline_access",
            })
        return httpx.Response(200, json={"path": request.url.path, "method": request.method})

    class MockClient(real_client):
        def __init__(self, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    httpx.AsyncClient = MockClient
    return real_client


async def _post_login_checks():
    status = await srv.session_status()
    print("session_status:", status)
    assert status["logged_in"] and status["expired"] is False

    captured = {}
    real_client = httpx.AsyncClient.__mro__[1]
    def handler2(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"compte": "ok"})
    class CapClient(real_client):
        def __init__(self, **kw):
            kw["transport"] = httpx.MockTransport(handler2)
            super().__init__(**kw)
    httpx.AsyncClient = CapClient

    data = await srv.call_private_api(PRIVATE_PATH)
    assert captured["auth"] == "Bearer AT-SECRET", captured
    print("private call:", PRIVATE_PATH, "->", data, "| auth header:", captured["auth"])

    pub = await srv.get_mobile_configuration()
    print("public call ok:", pub)

    out = await srv.logout()
    blocked = await srv.call_private_api(PRIVATE_PATH)
    assert "error" in blocked
    print("logout:", out, "| private call after logout refused:", blocked)


def _run_mocked():
    async def main():
        tools = await srv.mcp.list_tools()
        names = [t.name for t in tools]
        print("tools:", names)
        assert {"login", "logout", "session_status", "call_private_api", "get_mobile_configuration"} <= set(names)

        real_client = _install_token_mock()
        opened = {}
        srv.webbrowser.open = lambda url: opened.setdefault("url", url) or True

        login_task = asyncio.create_task(srv.login())
        await asyncio.sleep(1.0)
        q = parse_qs(urlparse(opened["url"]).query)
        assert q["code_challenge_method"][0] == "S256"
        async with real_client() as c:
            r = await c.get(f"{q['redirect_uri'][0]}?code=fake-code&state={q['state'][0]}")
            assert r.status_code == 200

        result = await login_task
        print("login:", result)
        assert result["logged_in"] and "AT-SECRET" not in json.dumps(result)

        await _post_login_checks()
        print("ALL TESTS PASSED")

    asyncio.run(main())


def _start_mock_idp():
    class IdPHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            if parsed.path.endswith("/oauth2/v2.0/authorize"):
                state = q.get("state", [""])[0]
                redirect = q.get("redirect_uri", [""])[0]
                body = LOGIN_PAGE.replace("{redirect}", redirect).replace("{state}", state).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", MOCK_IDP_PORT), IdPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run_webview():
    srv.ISSUER = f"http://127.0.0.1:{MOCK_IDP_PORT}"
    srv.CLIENT_ID = "test-client-id"
    srv.LOGIN_TIMEOUT_S = 180
    _install_token_mock()
    idp = _start_mock_idp()

    opened = {}
    srv.webbrowser.open = lambda url: opened.setdefault("url", url) or True

    loop = asyncio.new_event_loop()
    login_future = asyncio.run_coroutine_threadsafe(srv.login(), loop)
    threading.Thread(target=loop.run_forever, daemon=True).start()

    for _ in range(100):
        if "url" in opened:
            break
        threading.Event().wait(0.1)
    auth_url = opened.get("url")
    assert auth_url, "authorize URL was not generated"
    print("webview opening:", auth_url[:110], "...")
    print("sign in inside the webview with any test username/password")

    try:
        import webview
        window = webview.create_window("HQ connector test login", auth_url)
        login_future.add_done_callback(lambda f: threading.Timer(1.0, window.destroy).start())
        webview.start()
    except ImportError:
        print("pywebview not installed, falling back to system browser")
        import webbrowser
        webbrowser.open(auth_url)

    result = login_future.result(timeout=180)
    print("login:", result)
    assert result["logged_in"] and "AT-SECRET" not in json.dumps(result)

    checks = asyncio.run_coroutine_threadsafe(_post_login_checks(), loop)
    checks.result(timeout=60)
    idp.shutdown()
    loop.call_soon_threadsafe(loop.stop)
    print("ALL TESTS PASSED (webview)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--webview", action="store_true",
                        help="open a real webview on a local mock IdP, sign in, then run API calls")
    args = parser.parse_args()
    if args.webview:
        _run_webview()
    else:
        _run_mocked()


if __name__ == "__main__":
    main()
