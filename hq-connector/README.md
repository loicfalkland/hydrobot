# hq-connector

Custom MCP connector for the Hydro-Quebec mobile backend API (endpoints discovered in the
`com.hydroquebec.mf_android` APK). Exposes the API as MCP tools so a Mistral agent
(La Plateforme / Le Chat / Vibe) can call it.

## Tools

| Tool | Endpoint | Auth |
|---|---|---|
| `get_mobile_configuration` | `GET /mf/mobile/public/api/v4_0/configuration/json` | none (public) |
| `get_public_device_info` | `GET /mf/mobile/public/api/v4_0/appareils/obtenir/appareil/{HQ_TOKEN}` | none (public) |
| `get_notification_count_public` | `GET /mf/mobile/public/api/v4_0/notifications/count` | none (public) |
| `call_private_api` | any `/mf/mobile/prive/...` or `/wsapi/mobile/prive/...` path | your own Bearer token |
| `list_known_endpoints` | local catalog of endpoints from the APK | none |

`call_private_api` requires the caller's own access token from their own Hydro-Quebec
login (Azure AD B2C, OAuth2 + PKCE). It is used for a single request and never stored.

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
