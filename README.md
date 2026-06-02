# mcpDemo-Slack — Slack AI Agent with Slack MCP + Okta for AI Agents

An AI agent that interacts with a Slack workspace through Slack's hosted MCP server (`https://mcp.slack.com/mcp`), demonstrating integration with Okta for AI Agents (O4AA) for authentication and authorization.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                               │
│         Chat Interface (queries & streaming responses)       │
└────────────────────────┬────────────────────────────────────┘
                         │ SSE streaming
                         ▼
              ┌──────────────────────┐
              │   FastAPI Backend    │
              │  (Python / Uvicorn)  │
              │                      │
              │  ┌────────────────┐  │
              │  │  Agent Loop    │  │
              │  │  (Claude API)  │  │
              │  └───────┬────────┘  │
              │          │ MCP client│
              └──────────┼───────────┘
                         │ HTTPS + Bearer token
                         ▼
              ┌──────────────────────┐
              │  Slack MCP Server    │
              │  mcp.slack.com/mcp   │
              └──────────┬───────────┘
                         │ Slack API
                         ▼
              ┌──────────────────────┐
              │  Slack Workspace     │
              └──────────────────────┘

         [Phase 2: O4AA]
              ┌──────────────────────┐
              │  Okta for AI Agents  │
              │  (OAuth STS broker)  │
              └──────────────────────┘
```

Unlike the Atlassian variant, no subprocess MCP server is required — the agent connects directly to Slack's externally hosted MCP over HTTPS.

---

## Phase 1 — Connect and Test Slack MCP

### Prerequisites

- Slack workspace with a Slack app installed (user token with appropriate scopes)
- Python 3.12+
- Anthropic API key

### Required Slack token scopes

The `SLACK_MCP_TOKEN` (user token `xoxp-...`) needs at minimum:

| Scope | Purpose |
|---|---|
| `channels:read` | List public channels |
| `channels:history` | Read public channel messages |
| `groups:read` | List private channels |
| `groups:history` | Read private channel messages |
| `users:read` | Look up user info |
| `search:read` | Search messages |
| `chat:write` | Post messages (if needed) |

### Step 1 — Create a Slack app

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Under **OAuth & Permissions → User Token Scopes**, add the scopes above
3. Under **OAuth & Permissions → Redirect URLs**, add `http://localhost:8888/callback`
4. Install the app to your workspace
5. Note the **Client ID** and **Client Secret** from the app's Basic Information page

> **Note:** The Slack MCP server requires an OAuth 2.0 *user* access token (`xoxp-...`) obtained via
> the authorization code + PKCE flow — not a standard bot token.

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

## Phase 2 — Okta for AI Agents *(coming soon)*

Adds Okta OIDC user login and brokered token exchange so the agent uses per-user Slack credentials rather than a shared static token.

**Components to configure in Okta:**

| Component | Type | Purpose |
|---|---|---|
| Agentic App | OIDC Web App | User authentication → `id_token` |
| AI Agent | Workload Principal | Represents the backend agent |
| MCP Server resource | `STS_ACCESS_TOKEN / MCP_SERVER` | Slack OAuth client config |
| Managed Connection | Links agent → MCP server | Used in token exchange |

---

## Environment Variables

### Phase 1

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SLACK_MCP_TOKEN` | Slack user OAuth token (`xoxp-...`) |
| `SESSION_SECRET` | Session signing secret |

### Phase 2 (coming soon)

| Variable | Description |
|---|---|
| `OKTA_DOMAIN` | Okta org domain (no `https://`) |
| `OKTA_CLIENT_ID` | Agentic App client ID |
| `OKTA_CLIENT_SECRET` | Agentic App client secret |
| `OKTA_REDIRECT_URI` | OAuth callback URL |
| `OKTA_AGENT_CLIENT_ID` | AI Agent workload client ID |
| `OKTA_AGENT_PRIVATE_JWK` | Agent RSA private key (JSON string) |
| `OKTA_MCP_RESOURCE_INDICATOR` | ORN of the Slack MCP Server in Okta |
