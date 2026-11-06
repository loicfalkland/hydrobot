"""Integration test against the real Hydro-Quebec backend using YOUR OWN login.

Run on your machine (the sandbox has no outbound network):

    pip install -r requirements.txt
    export HQ_BASE_URL=https://<backend-host>          # e.g. the dev backend from the APK
    export HQ_ISSUER=https://connexion-<env>.solutions.hydroquebec.com/tfp/<tenant>/b2c_1a_<policy>/v2.0
    export HQ_CLIENT_ID=<client-id-from-public-config>
    python integration_test.py

The login tool opens your browser/webview; sign in with your own credentials.
Only read-only GET endpoints are called. Tokens are never printed or written to disk.
"""
import asyncio
import json

import hq_mcp_server as srv

READ_ONLY_PRIVATE_PATHS = [
    "/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte",
    "/wsapi/mobile/prive/api/v1_0/relations",
]


async def main():
    print("[1] public: get_mobile_configuration")
    try:
        config = await srv.get_mobile_configuration()
        print("    ok, keys:", sorted(config.keys())[:8])
    except Exception as exc:
        print("    FAILED:", str(exc)[:300])
        print("    hint: the backend may require the app headers (Accept: */*, GUID_SESSION) or a ?fichier= param;")
        print("    try: curl -i '<HQ_BASE_URL>/mf/mobile/public/api/v4_0/configuration/json' on your machine to inspect the 400 body.")
        return

    print("[2] public: list_known_endpoints")
    eps = await srv.list_known_endpoints()
    print("    ok, base_url:", eps["base_url"])

    print("[3] login (a browser/webview will open - sign in with YOUR credentials)")
    result = await srv.login()
    print("   ", result)
    assert result.get("logged_in"), "login failed"
    assert "access_token" not in json.dumps(result), "token leaked in output"

    print("[4] session_status")
    print("   ", await srv.session_status())

    print("[5] private read-only calls with the session token")
    for path in READ_ONLY_PRIVATE_PATHS:
        try:
            data = await srv.call_private_api(path)
            summary = {k: type(v).__name__ for k, v in list(data.items())[:5]} if isinstance(data, dict) else type(data).__name__
            print(f"    {path} -> ok ({summary})")
        except Exception as exc:
            print(f"    {path} -> {type(exc).__name__}: {str(exc)[:120]}")

    print("[6] logout")
    print("   ", await srv.logout())
    blocked = await srv.call_private_api(READ_ONLY_PRIVATE_PATHS[0])
    assert "error" in blocked
    print("    private call after logout refused as expected")
    print("INTEGRATION TEST DONE")


if __name__ == "__main__":
    asyncio.run(main())
