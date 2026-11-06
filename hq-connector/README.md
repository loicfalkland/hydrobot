# hq-connector

Custom MCP connector for the Hydro-Quebec mobile backend API (endpoints discovered in the
`com.hydroquebec.mf_android` APK). Exposes the API as MCP tools so a Mistral agent
(La Plateforme / Le Chat / Vibe) can call it.

## Tools

| Tool | Endpoint / purpose | Auth |
|---|---|---|
| `login` | opens Azure AD B2C authorize page in the browser/webview, captures code on loopback redirect, exchanges it (PKCE + state) | interactive, user's own credentials |
| `refresh_access_token` | refresh-token grant | session |
| `logout` / `session_status` | clear / inspect in-memory session (no token values exposed) | — |
| `get_mobile_configuration` | `GET /mf/mobile/public/api/v4_0/configuration/json` | none (public) |
| `get_public_device_info` | `GET /mf/mobile/public/api/v4_0/appareils/obtenir/appareil/{HQ_TOKEN}` | none (public) |
| `get_notification_count_public` | `GET /mf/mobile/public/api/v4_0/notifications/count` | none (public) |
| `call_private_api` | any `/mf/mobile/prive/...` or `/wsapi/mobile/prive/...` path | session token from `login` (or your own Bearer token for one request) |
| `list_known_endpoints` | local catalog of endpoints from the APK | none |

## Webview login flow

Call the `login` tool: the server starts a loopback listener on `127.0.0.1:8976` and opens
the Azure AD B2C authorize URL (`{issuer}/oauth2/v2.0/authorize`) in the system browser
(or an embedded webview in a desktop client). The user signs in with their own Hydro-Québec
credentials; the provider redirects to `http://127.0.0.1:8976/callback?code=...&state=...`.
The server validates `state`, exchanges the code with PKCE (`S256`) at
`{issuer}/oauth2/v2.0/token`, and keeps the tokens **in memory only** — they are never
logged and never returned in tool output (only metadata: token_type, scope, expires_in).
`call_private_api` then uses the session token automatically; `refresh_access_token` rotates
it; `logout` clears it.

OAuth2 settings come from env (`HQ_ISSUER`, `HQ_CLIENT_ID`, `HQ_SCOPES`, `HQ_REDIRECT_URI`)
or are read from the public configuration JSON's `oauth2Config`. In Mistral Studio, if you
register this server as a connector with OAuth2 auth, the platform runs this same flow in
its own webview — no code change needed.

## Run locally

```bash
pip install -r requirements.txt
HQ_BASE_URL=https://<backend-host> python hq_mcp_server.py   # streamable-http on :8000/mcp
```

`HQ_BASE_URL` defaults to the dev backend bundled in the APK; set it to the production
backend host (the app fetches it at runtime from the public configuration JSON).

## Host it

Mistral's platform calls your connector over HTTPS, so deploy this server somewhere
reachable (your own infra, e.g. behind a reverse proxy) at `https://<your-host>/mcp`.

## Register as a Mistral connector

```python
from mistralai.client import Mistral
client = Mistral(api_key="MISTRAL_API_KEY")

connector = await client.beta.connectors.create_async(request={
    "protocol": "mcp",
    "name": "hq_mobile_api",
    "description": "Hydro-Quebec mobile backend API",
    "server": "https://<your-host>/mcp",
    "visibility": "private",   # or workspace / organization
})
```

Or via REST: `POST https://api.mistral.ai/v1/connectors` with
`{"name": "hq_mobile_api", "description": "...", "server": "https://<your-host>/mcp"}`.
If the server needs auth, add credentials in Studio (Connectors → Credentials) or use
the OAuth2 flow.

## Use it with an agent

```python
response = await client.beta.conversations.start_async(
    model="mistral-medium-latest",
    inputs="Fetch the mobile configuration and summarize the minimum app versions",
    tools=[{"type": "connector", "connector_id": "hq_mobile_api"}],
)
```

Or attach the connector when creating an agent via `POST /v1/agents` /
`client.beta.agents.create(..., tools=[{"type": "connector", "connector_id": "hq_mobile_api"}])`.

## Notes

- The identity provider is Azure AD B2C
  (`https://connexion-<env>.solutions.hydroquebec.com/tfp/<tenant>/b2c_1a_<policy>/v2.0`);
  tokens are obtained with OAuth2 + PKCE, redirect `msauth://com.hydroquebec.mf_android`.
- Only public endpoints and self-authenticated private calls are exposed. Do not use
  this connector with tokens or HQ_TOKENs you do not own.
