"""Integration test against the REAL Hydro-Quebec backend with YOUR OWN login.

Run on your machine (needs outbound network):

    pip install -r requirements.txt
    pip install pywebview            # only for --webview
    export HQ_BASE_URL=https://<backend-host>          # e.g. dev-backend.dev01.cl.solutions.hydroquebec.com
    export HQ_ISSUER=https://connexion-<env>.solutions.hydroquebec.com/tfp/<tenant>/b2c_1a_<policy>/v2.0
    export HQ_CLIENT_ID=<client-id from the public configuration JSON>
    python integration_test.py --webview     # real HQ login page in an embedded webview
    python integration_test.py               # system browser / printed URL fallback

Flow: public config -> login (you enter YOUR credentials in the webview) ->
session_status -> private read-only calls reusing the session token ->
refresh_access_token -> private call again -> logout -> verify blocked.

Only read-only GET endpoints are called. Tokens are kept in memory only and are
never printed, logged, or written to disk. Never share your password or tokens.
"""
import argparse
import asyncio
import json
import os
import sys
import threading

import hq_mcp_server as srv

READ_ONLY_PRIVATE_PATHS = [
    "/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte",
    "/wsapi/mobile/prive/api/v1_0/relations",
    "/wsapi/mobile/prive/api/v3_0/factures",
]


def _summary(data) -> str:
    if isinstance(data, dict):
        return "{" + ", ".join(f"{k}: {type(v).__name__}" for k, v in list(data.items())[:5]) + "}"
    return type(data).__name__


async def main(use_webview: bool):
    print("[1] public: get_mobile_configuration (real server)")
    config = await srv.get_mobile_configuration()
    print("    ok, top-level keys:", sorted(config.keys())[:8])
    oauth = config.get("oauth2Config", {})
    if oauth.get("issuer") and not os.environ.get("HQ_ISSUER"):
        srv.ISSUER = oauth["issuer"].rstrip("/")
    if oauth.get("clientId") and not os.environ.get("HQ_CLIENT_ID"):
        srv.CLIENT_ID = oauth["clientId"]
    print("    using issuer:", srv.ISSUER or os.environ.get("HQ_ISSUER"))

    if use_webview:
        import webview
        opened = {}
        srv.webbrowser.open = lambda url: opened.setdefault("url", url) or True
        login_task = asyncio.create_task(srv.login())
        for _ in range(100):
            if "url" in opened:
                break
            await asyncio.sleep(0.1)
        auth_url = opened.get("url")
        assert auth_url, "authorize URL not generated"
        print("[2] login: opening REAL Hydro-Quebec page in webview")
        print("    ", auth_url[:110], "...")
        window = webview.create_window("Hydro-Quebec sign-in", auth_url)
        login_task.add_done_callback(lambda t: threading.Timer(1.5, window.destroy).start())
        threading.Thread(target=webview.start, daemon=True).start()
        result = await login_task
    else:
        print("[2] login: browser/printed URL (set --webview for an embedded webview)")
        result = await srv.login()

    print("    login:", result)
    assert result.get("logged_in"), "login failed"
    assert "access_token" not in json.dumps(result), "token leaked in output"

    print("[3] session_status")
    status = await srv.session_status()
    print("   ", status)
    assert status["logged_in"]

    print("[4] private read-only calls reusing the session token")
    for path in READ_ONLY_PRIVATE_PATHS:
        try:
            data = await srv.call_private_api(path)
            print(f"    {path} -> ok {_summary(data)}")
        except Exception as exc:
            print(f"    {path} -> {type(exc).__name__}: {str(exc)[:150]}")

    print("[5] refresh_access_token, then call again with the new token")
    refreshed = await srv.refresh_access_token()
    print("   ", refreshed)
    assert refreshed.get("logged_in")
    try:
        data = await srv.call_private_api(READ_ONLY_PRIVATE_PATHS[0])
        print(f"    {READ_ONLY_PRIVATE_PATHS[0]} -> ok {_summary(data)}")
    except Exception as exc:
        print(f"    -> {type(exc).__name__}: {str(exc)[:150]}")

    print("[6] logout and verify private calls are refused")
    print("   ", await srv.logout())
    blocked = await srv.call_private_api(READ_ONLY_PRIVATE_PATHS[0])
    assert "error" in blocked
    print("    refused as expected:", blocked)
    print("INTEGRATION TEST DONE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--webview", action="store_true",
                        help="open the real Hydro-Quebec login page in an embedded webview (pywebview)")
    args = parser.parse_args()
    if args.webview:
        try:
            import webview  # noqa: F401
        except ImportError:
            sys.exit("pywebview not installed: pip install pywebview")
    asyncio.run(main(args.webview))
