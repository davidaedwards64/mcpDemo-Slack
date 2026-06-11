# mcpDemo-Slack — Slack AI Agent with Slack MCP + Okta for AI Agents

An AI agent that interacts with a Slack workspace through Slack's hosted MCP server (`https://mcp.slack.com/mcp`), demonstrating integration with Okta for AI Agents (O4AA) for authentication and authorization.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Browser                                     │
│                Chat Interface + Okta Login                           │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │ SSE / chat request                    │ [Phase 2] OIDC login
           ▼                                       ▼
┌──────────────────────────┐          ┌────────────────────────────────┐
│    FastAPI Backend       │ RFC 8693 │     Okta for AI Agents         │
│   (Python / Uvicorn)     │─────────►│                                │
│                          │◄─────────│  • OIDC Web App (user auth)    │
│  ┌────────────────────┐  │  Slack   │  • AI Agent (workload princ.)  │
│  │    Agent Loop      │  │  token   │  • MCP Server resource         │
│  │    (Claude API)    │  │          │  • Managed Connection          │
│  └─────────┬──────────┘  │          └────────────────────────────────┘
└────────────┼─────────────┘
             │ HTTPS + Bearer token
             │ (per-user xoxp · Phase 2, or static token · Phase 1)
             ▼
┌──────────────────────────┐
│   Slack MCP Server       │
│   mcp.slack.com/mcp      │
└────────────┬─────────────┘
             │ Slack API
             ▼
┌──────────────────────────┐
│   Slack Workspace        │
└──────────────────────────┘
```

Unlike the Atlassian variant, no subprocess MCP server is required — the agent connects directly to Slack's externally hosted MCP over HTTPS.

---

## Phase 1 — Connect and Test Slack MCP

### Prerequisites

- Slack workspace with a Slack app installed (user token with appropriate scopes)
- Python 3.12+
- Anthropic API key

### Required Slack token scopes

The Slack MCP server requires a user token (`xoxp-...`) with the following scopes. These are also the scopes you must list in the **Okta MCP Server resource definition** if using Phase 2.

| Scope | Purpose |
|---|---|
| `channels:read` | List public channels |
| `channels:history` | Read public channel messages |
| `groups:read` | List private channels |
| `groups:history` | Read private channel messages |
| `im:read` | List direct message channels |
| `im:history` | Read direct message history |
| `mpim:read` | List group DM channels |
| `mpim:history` | Read group DM message history |
| `users:read` | Look up user info |
| `users:read.email` | Look up users by email address |
| `search:read` | Search messages across channels |
| `team:read` | Get workspace info |
| `chat:write` | Post messages |
| `reactions:write` | Add emoji reactions |
| `reminders:write` | Create reminders |

### Step 1 — Create a Slack app

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) and create a new app (choose **From scratch**)
2. Under **Agents & AI Tools → Agents**, enable **MCP (Model Context Protocol)**. This unlocks the app's ability to serve as an MCP client against `mcp.slack.com/mcp`.
3. Under **OAuth & Permissions → User Token Scopes**, add all the scopes from the table above
4. Under **OAuth & Permissions → Redirect URLs**, add `http://localhost:8888/callback`
5. Install the app to your workspace
6. Note the **Client ID** and **Client Secret** from the app's **Basic Information** page

> **Note:** The Slack MCP server requires an OAuth 2.0 *user* access token (`xoxp-...`) obtained via
> the authorization code + PKCE flow — not a standard bot token.

> **Scope alignment (Phase 2):** If you are configuring Okta for AI Agents, the scopes listed in your
> **Okta MCP Server resource definition must exactly match** the User Token Scopes you added above.
> Okta requests those specific scopes from Slack during the STS token exchange; any mismatch causes
> the exchange to fail with an `interaction_required` error.

### Step 2 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set:
```
ANTHROPIC_API_KEY=sk-ant-...
SESSION_SECRET=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
SLACK_CLIENT_ID=<your app client ID>
SLACK_CLIENT_SECRET=<your app client secret>
```

### Step 3 — Obtain a Slack user token

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/get_slack_token.py
```

This opens a browser window for Slack authorization. After you approve, it saves
`SLACK_MCP_TOKEN` (and `SLACK_REFRESH_TOKEN`) to your `.env` automatically.

### Step 4 — Run the smoke test

```bash
python3 scripts/smoke_test.py
```

**Acceptance criteria for Phase 1:**
- [ ] MCP session initializes successfully against `https://mcp.slack.com/mcp`
- [ ] Tool list is non-empty
- [ ] `Smoke test passed — Phase 1 complete.`

### Step 5 — Run the full app locally

```bash
uvicorn backend.main:app --reload
# Open http://localhost:8000
```

Try asking: *"What channels are in my workspace?"* or *"Show me the last few messages in #general"*

---

## Deployment on Render

Deploy as a **Web Service** with:

- **Runtime:** Python 3
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

### Render environment variables (Phase 1)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SLACK_MCP_TOKEN` | Slack user OAuth token (`xoxp-...`) |
| `SESSION_SECRET` | Random hex string (32 bytes) |

---

## Phase 2 — Okta for AI Agents

Replaces the shared static `SLACK_MCP_TOKEN` with per-user Slack credentials brokered by Okta for AI Agents (O4AA). Each user authenticates via OIDC and the backend exchanges their `id_token` for a short-lived Slack access token using [RFC 8693 token exchange](https://www.rfc-editor.org/rfc/rfc8693).

### How it works

```
User  →  [Okta OIDC login]  →  id_token
Backend  →  [RFC 8693 STS exchange]  →  Slack access token (xoxp-...)
Backend  →  [Slack MCP over HTTPS]  →  Slack workspace
```

1. The user logs in via Okta (authorization code flow). The backend stores the `id_token` in the session.
2. On each chat request, the backend signs a short-lived `client_assertion` JWT using the AI Agent's RSA private key.
3. The backend calls Okta's token endpoint requesting a token exchange — presenting the user's `id_token` as the subject token and the agent's `client_assertion` as the client credential.
4. Okta validates the request and returns a Slack access token scoped to the configured MCP Server resource.
5. The backend authenticates to `mcp.slack.com/mcp` with this per-user token. Tokens are cached in memory (keyed by user sub) until near expiry.

### Okta configuration

Four objects are required in Okta for AI Agents:

#### 1. Agentic App (OIDC Web App)

Create an OIDC Web App. This handles user login and issues the `id_token`.

- **Grant type:** Authorization Code
- **Sign-in redirect URI:** `https://<your-domain>/auth/callback`
- **Sign-out redirect URI:** `https://<your-domain>/auth/signin`
- **Scopes:** `openid email profile`

Note the **Client ID** → `OKTA_CLIENT_ID` and **Client Secret** → `OKTA_CLIENT_SECRET`.

#### 2. AI Agent (Workload Principal)

Create an AI Agent under **Okta for AI Agents → AI Agents**.

- Generate an RSA key pair and register the **public key** with the workload principal.
- Note the **Client ID** → `OKTA_AGENT_CLIENT_ID`.
- Store the **private key** as a JWK JSON string → `OKTA_AGENT_PRIVATE_JWK`.

The backend uses this key to sign `client_assertion` JWTs (RS256) when calling the Okta token endpoint.

#### 3. MCP Server resource

Create a resource of type **MCP Server** (under **Okta for AI Agents → MCP Servers** or equivalent).

- **External client:** enter your Slack app's **Client ID** and **Client Secret**.
- **Scopes:** add **exactly the same scopes** listed in [Required Slack token scopes](#required-slack-token-scopes). Okta requests these specific scopes from Slack during the token exchange — any mismatch causes the exchange to fail or prompt the user for re-consent.

Note the **ORN** (Okta Resource Name) of the resource → `OKTA_MCP_RESOURCE_INDICATOR`.

#### 4. Managed Connection

Create a **Managed Connection** linking the AI Agent to the MCP Server resource.

- **From:** the AI Agent workload principal
- **To:** the MCP Server resource

This connection authorizes the AI Agent to broker Slack tokens on behalf of users.

### User consent (interaction_required)

On first login, Okta may return `interaction_required` — the user has not yet granted the agent permission to access their Slack. The UI surfaces a link the user must click to complete consent via Okta. After consent, subsequent requests use the cached token with no additional interaction.

### Render environment variables (Phase 2)

Add these in addition to the Phase 1 variables (`ANTHROPIC_API_KEY`, `SESSION_SECRET`). `SLACK_MCP_TOKEN` is not required when Phase 2 is fully configured — it is only used as a fallback when no `id_token` is present.

| Variable | Description |
|---|---|
| `OKTA_DOMAIN` | Okta org domain without `https://` (e.g. `your-org.okta.com`) |
| `OKTA_CLIENT_ID` | Agentic App client ID |
| `OKTA_CLIENT_SECRET` | Agentic App client secret |
| `OKTA_REDIRECT_URI` | OAuth callback URL (e.g. `https://<your-render-domain>/auth/callback`) |
| `OKTA_AGENT_CLIENT_ID` | AI Agent workload principal client ID |
| `OKTA_AGENT_PRIVATE_JWK` | Agent RSA private key as a JWK JSON string |
| `OKTA_MCP_RESOURCE_INDICATOR` | ORN of the Slack MCP Server resource in Okta |

---

## Environment Variables

### Phase 1

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SLACK_MCP_TOKEN` | Slack user OAuth token (`xoxp-...`) |
| `SESSION_SECRET` | Session signing secret |

### Phase 2

| Variable | Description |
|---|---|
| `OKTA_DOMAIN` | Okta org domain without `https://` |
| `OKTA_CLIENT_ID` | Agentic App (OIDC Web App) client ID |
| `OKTA_CLIENT_SECRET` | Agentic App client secret |
| `OKTA_REDIRECT_URI` | OAuth callback URL |
| `OKTA_AGENT_CLIENT_ID` | AI Agent workload principal client ID |
| `OKTA_AGENT_PRIVATE_JWK` | Agent RSA private key as a JWK JSON string |
| `OKTA_MCP_RESOURCE_INDICATOR` | ORN of the Slack MCP Server resource in Okta |
