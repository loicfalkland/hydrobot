"""Mock-based smoke test for hq_mcp_server. Run: python test_connector.py"""
import asyncio
import json
import os
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("HQ_ISSUER", "https://connexion-nonprod.example.com/tfp/tenant/b2c_1a_test/v2.0")
os.environ.setdefault("HQ_CLIENT_ID", "test-client-id")

import httpx
import hq_mcp_server as srv


def main():
    asyncio.run(_run())


async def _run():
    tools = await srv.mcp.list_tools()
    names = [t.name for t in tools]
    print("tools:", names)
    assert {"login", "logout", "session_status", "call_private_api", "get_mobile_configuration"} <= set(names)

    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={
                "access_token": "AT-SECRET", "refresh_token": "RT-SECRET",
                "token_type": "Bearer", "expires_in": 3600, "scope": "openid offline_access",
            })
        return httpx.Response(200, json={"path": request.url.path})

    class MockClient(real_client):
        def __init__(self, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(**kw)

    httpx.AsyncClient = MockClient
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

    captured = {}
    def handler2(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})
    httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(handler2), **kw)

    r = await srv.call_private_api("/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte")
    assert captured["auth"] == "Bearer AT-SECRET", captured
    print("private call auth header:", captured["auth"])

    await srv.logout()
    r = await srv.call_private_api("/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte")
    assert "error" in r
    print("after logout, private call refused:", r)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
