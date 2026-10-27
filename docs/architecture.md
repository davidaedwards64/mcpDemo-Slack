# mcpDemo-Slack — Architecture

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════╗
║                         SLACK AI AGENT — mcpDemo-Slack — ARCHITECTURE                          ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                                                        │
│  ┌────────────────────────────────────────────┐  ┌──────────────────────────────────────────┐  │
│  │  signin.html                               │  │  index.html                              │  │
│  │  "Sign in with Okta" → /auth/start         │  │  GET /api/me     → populate user info    │  │
│  │                                            │  │  POST /api/chat  → consume SSE stream    │  │
│  │                                            │  │  auth flow panel → interaction_req link  │  │
│  │                                            │  │  /api/chat/clear → reset history         │  │
│  └────────────────────────────────────────────┘  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
           │  HTTPS (session cookie) ①②                 │  SSE stream (text/event-stream) ⑥
           ▼                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND  (Python / Uvicorn)                                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │  GET /auth/start    → redirect → Okta /v1/authorize                        ──────────①  │   │
│  │  GET /auth/callback ← code → POST Okta /v1/token → store id_token in session  ──────②  │   │
│  │  GET /auth/logout   → clear session + STS cache → Okta /v1/logout                      │   │
│  │  GET /api/me        → return user from session (401 if unauthenticated)                 │   │
│  │  POST /api/chat     → run_agent(message, id_token, sub, history)           ──────────⑥  │   │
│  │  POST /api/chat/clear → drop in-memory history[sub]                                     │   │
│  └──────────────────────────────────┬──────────────────────────────────────────────────────┘   │
│                                     │                                                           │
│  agent.py                           │                                                           │
│  ┌──────────────────────────────────▼──────────────────────────────────────────────────────┐   │
│  │  1. yield status "Authenticating with Slack..."                                         │   │
│  │  2. exchange_id_token_for_slack_token(id_token, cache=sub)               ──────────③   │   │
│  │     → token_meta{step=sts, expires_in, cached}                                         │   │
│  │     → on interaction_required: yield event + return                                    │   │
│  │  3. streamablehttp_client(mcp.slack.com/mcp, Bearer <xoxp>)              ──────────④   │   │
│  │     mcp.initialize() + list_tools()  (~24 tools)                                       │   │
│  │  4. agentic loop (max 10): messages.stream(claude-opus-4-7, tools)        ──────────⑤   │   │
│  │     yield text · on tool_use: yield tool + mcp.call_tool() → tool_result               │   │
│  │  5. yield done{}                                                                        │   │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
    │ ①②                    │ ③                     │ ④                    │ ⑤
    ▼                         ▼                        ▼                      ▼
  ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
  │  OKTA (OIDC)       │ │ OKTA FOR AI AGENTS │ │  SLACK MCP SERVER  │ │  ANTHROPIC API     │
  │  ────────────────  │ │ ─────────────────  │ │  ────────────────  │ │  ────────────────  │
  │  /v1/authorize     │ │ STS / RFC 8693     │ │  mcp.slack.com/mcp │ │  claude-opus-4-7   │
  │  /v1/token         │ │ POST /v1/token     │ │  Streamable HTTP   │ │  messages.stream   │
  │  /v1/logout        │ │                    │ │  ~24 tools         │ │                    │
  │                    │ │ subject_token=     │ │                    │ │  • tool use        │
  │  Agentic App       │ │   id_token         │ │  Auth: Bearer      │ │  • streaming       │
  │  (OIDC Web App)    │ │ req=oauth-sts      │ │    <xoxp-token>    │ │  • max_tokens=4096 │
  │                    │ │ client_secret auth │ │                    │ │                    │
  │  Issues:           │ │                    │ │  channels/msgs     │ │  max_iterations    │
  │  • id_token        │ │ → xoxp-... token   │ │  users/DMs         │ │  = 10              │
  │  • auth code       │ │ → interaction_uri  │ │                    │ │                    │
  └────────────────────┘ └────────────────────┘ └────────────────────┘ └────────────────────┘
                                                         │ ⑦ Slack API
                                                         ▼
                                                ┌────────────────────┐
                                                │  SLACK WORKSPACE   │
                                                │  ────────────────  │
                                                │  channels          │
                                                │  messages          │
                                                │  users / DMs       │
                                                └────────────────────┘


KEY FLOWS
─────────
① Sign-in:    Browser → /auth/start → Okta /v1/authorize (OIDC code flow)
              → /auth/callback → POST Okta /v1/token (code → id_token)
              → store {id_token, sub, email, name} in session cookie → redirect /

② Per-request token exchange (RFC 8693):
              /api/chat reads id_token from session
              → okta_sts.py authenticates with client_id/client_secret (form body)
              → POST Okta /v1/token: id_token + resource ORN
              → Okta validates user + AI Agent identity + Managed Connection
              → returns xoxp-... token (cached by sub, TTL=expires_in−60s)

③ interaction_required: First login may return interaction_uri
              → agent yields interaction_required SSE event → UI shows consent link
              → after consent, next request succeeds and token is cached

④ MCP:        agent.py opens Streamable HTTP to mcp.slack.com/mcp
              Authorization: Bearer <xoxp-token>
              mcp.initialize() + list_tools() → ~24 Slack tools exposed to Claude

⑤ Agentic loop: Claude receives message + history + tool definitions → streams text
              agent.py dispatches tool_use to mcp.call_tool() → Slack API result
              loop continues until stop_reason ≠ tool_use (max 10 iterations)

⑥ SSE events: status · text · tool · token_meta · done · error · interaction_required

⑦ Slack API:  Slack MCP Server translates MCP calls to Slack REST API
              → reads/writes channels, messages, users, DMs
```
