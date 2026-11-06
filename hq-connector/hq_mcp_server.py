import asyncio
import base64
import hashlib
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get(
    "HQ_BASE_URL",
    "https://dev-backend.dev01.cl.solutions.hydroquebec.com",
).rstrip("/")

ISSUER = os.environ.get("HQ_ISSUER", "").rstrip("/")
CLIENT_ID = os.environ.get("HQ_CLIENT_ID", "")
SCOPES = os.environ.get("HQ_SCOPES", "openid offline_access")
REDIRECT_URI = os.environ.get("HQ_REDIRECT_URI", "http://127.0.0.1:8976/callback")
LOGIN_TIMEOUT_S = int(os.environ.get("HQ_LOGIN_TIMEOUT_S", "300"))

mcp = FastMCP("hydroquebec-mobile-api")

TIMEOUT = 30.0

_session: dict = {"access_token": None, "refresh_token": None, "expires_at": 0.0}


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _get(path: str, params: dict | None = None, bearer: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(f"{BASE_URL}{path}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def _resolve_oauth_config() -> tuple[str, str, str]:
    issuer, client_id, scopes = ISSUER, CLIENT_ID, SCOPES
    if not issuer or not client_id:
        config = await get_mobile_configuration()
        oauth = config.get("oauth2Config", {})
        issuer = issuer or oauth.get("issuer", "").rstrip("/")
        client_id = client_id or oauth.get("clientId", "")
        scopes = oauth.get("scopes", scopes)
    if not issuer or not client_id:
        raise RuntimeError("OAuth2 issuer/client_id not configured (set HQ_ISSUER and HQ_CLIENT_ID or expose them via the public configuration JSON)")
    return issuer, client_id, scopes


async def _exchange_code(issuer: str, client_id: str, code: str, verifier: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{issuer}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "scope": SCOPES,
            },
        )
        response.raise_for_status()
        return response.json()


async def _refresh(issuer: str, client_id: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{issuer}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": _session["refresh_token"],
                "scope": SCOPES,
            },
        )
        response.raise_for_status()
        return response.json()


def _store_tokens(token_response: dict) -> dict:
    _session["access_token"] = token_response.get("access_token")
    if token_response.get("refresh_token"):
        _session["refresh_token"] = token_response["refresh_token"]
    _session["expires_at"] = time.time() + int(token_response.get("expires_in", 3600))
    return {
        "logged_in": True,
        "token_type": token_response.get("token_type"),
        "scope": token_response.get("scope"),
        "expires_in": token_response.get("expires_in"),
    }


@mcp.tool()
async def login() -> dict:
    """Interactive login via webview/browser: opens the Azure AD B2C authorize page
    (OAuth2 authorization code + PKCE, loopback redirect on 127.0.0.1), captures the
    authorization code, exchanges it for tokens, and keeps them in memory only.
    Returns token metadata only, never the tokens themselves."""
    issuer, client_id, scopes = await _resolve_oauth_config()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)

    auth_url = f"{issuer}/oauth2/v2.0/authorize?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    result: dict = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            if "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Login complete. You can close this tab.")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 8976), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webbrowser.open(auth_url)
        deadline = time.time() + LOGIN_TIMEOUT_S
        while time.time() < deadline and "code" not in result and "error" not in result:
            await asyncio.sleep(0.5)
    finally:
        server.shutdown()
        thread.join(timeout=5)

    if "error" in result:
        return {"logged_in": False, "error": result["error"]}
    if "code" not in result:
        return {"logged_in": False, "error": "timeout waiting for login callback"}

    tokens = await _exchange_code(issuer, client_id, result["code"], verifier)
    return _store_tokens(tokens)


@mcp.tool()
async def refresh_access_token() -> dict:
    """Exchange the stored refresh token for a new access token. Run login first."""
    if not _session["refresh_token"]:
        return {"logged_in": False, "error": "no refresh token, call login first"}
    issuer, client_id, _ = await _resolve_oauth_config()
    tokens = await _refresh(issuer, client_id)
    return _store_tokens(tokens)


@mcp.tool()
async def logout() -> dict:
    """Clear the in-memory session tokens."""
    _session["access_token"] = None
    _session["refresh_token"] = None
    _session["expires_at"] = 0.0
    return {"logged_in": False}


@mcp.tool()
async def session_status() -> dict:
    """Whether a session is active and when the access token expires (no token values exposed)."""
    return {
        "logged_in": _session["access_token"] is not None,
        "expired": time.time() >= _session["expires_at"] if _session["access_token"] else None,
    }


@mcp.tool()
async def get_mobile_configuration() -> dict:
    """Fetch the Hydro-Quebec mobile app public configuration JSON (version rules, OAuth2 config, service URLs). Public endpoint, no auth."""
    return await _get("/mf/mobile/public/api/v4_0/configuration/json")


@mcp.tool()
async def get_public_device_info(hq_token: str) -> dict:
    """Look up public device info by HQ_TOKEN (device token issued at registration). Public endpoint, no auth."""
    return await _get(f"/mf/mobile/public/api/v4_0/appareils/obtenir/appareil/{hq_token}")


@mcp.tool()
async def get_notification_count_public(hq_token: str, langue: str = "fr") -> dict:
    """Get the public notification count for a device HQ_TOKEN. Public endpoint, no auth."""
    return await _get(
        "/mf/mobile/public/api/v4_0/notifications/count",
        params={"hqToken": hq_token, "langue": langue},
    )


@mcp.tool()
async def call_private_api(
    path: str,
    bearer_token: str = "",
    params: dict | None = None,
) -> dict:
    """Call an authenticated (prive) mobile API endpoint, e.g. /wsapi/mobile/prive/api/v3_0/partenaires/infoCompte.

    Uses the session token from the login tool when bearer_token is omitted.
    bearer_token, if given, must be YOUR OWN access token from your own Hydro-Quebec
    login. Never pass a token you do not own. Tokens are used for this request only
    and are never stored or logged."""
    if not path.startswith("/"):
        path = "/" + path
    token = bearer_token or _session["access_token"]
    if not token:
        return {"error": "no session, call the login tool first"}
    return await _get(path, params=params, bearer=token)


@mcp.tool()
async def list_known_endpoints() -> dict:
    """List the Hydro-Quebec mobile backend endpoint groups discovered in the app (public, private, auth issuer)."""
    return {
        "base_url": BASE_URL,
        "public": [
            "/mf/mobile/public/api/v4_0/configuration/json",
            "/mf/mobile/public/api/v4_0/appareils/obtenir/appareil/{HQ_TOKEN}",
            "/mf/mobile/public/api/v4_0/appareils/ajout",
            "/mf/mobile/public/api/v4_0/notifications?langue={lang}&hqToken={HQ_TOKEN}",
            "/mf/mobile/public/api/v4_0/notifications/count?hqToken={HQ_TOKEN}",
        ],
        "private_examples": [
            "/mf/mobile/prive/api/v3_0/appareils/auth/obtenir/appareils?hqToken={HQ_TOKEN}",
            "/wsapi/mobile/prive/api/v3_0/partenaires/infoCompte",
            "/wsapi/mobile/prive/api/v3_0/factures",
            "/wsapi/mobile/prive/api/v1_0/relations",
        ],
        "identity_issuer_example": "https://connexion-<env>.solutions.hydroquebec.com/tfp/<tenant>/b2c_1a_<policy>/v2.0 (Azure AD B2C, OAuth2 + PKCE, redirect msauth://com.hydroquebec.mf_android)",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
