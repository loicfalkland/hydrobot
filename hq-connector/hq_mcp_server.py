import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get(
    "HQ_BASE_URL",
    "https://dev-backend.dev01.cl.solutions.hydroquebec.com",
).rstrip("/")

mcp = FastMCP("hydroquebec-mobile-api")

TIMEOUT = 30.0


async def _get(path: str, params: dict | None = None, bearer: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(f"{BASE_URL}{path}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()


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
    bearer_token: str,
    params: dict | None = None,
) -> dict:
    """Call an authenticated (prive) mobile API endpoint, e.g. /wsapi/mobile/prive/api/v3_0/partenaires/infoCompte.

    bearer_token must be YOUR OWN access token obtained from your own Hydro-Quebec login
    (Azure AD B2C, OAuth2+PKCE). Never pass a token you do not own or that was taken from
    another user or device. The token is used only for this single request and is not stored.
    """
    if not path.startswith("/"):
        path = "/" + path
    return await _get(path, params=params, bearer=bearer_token)


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
