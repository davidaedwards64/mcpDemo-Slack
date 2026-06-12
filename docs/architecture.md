# mcpDemo-Slack — Architecture

```
╔═════════════════════════════════════════════════════════════════════════════════════════════════╗
║                         SLACK AI AGENT — mcpDemo-Slack — ARCHITECTURE                          ║
╚═════════════════════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                                                        │
│                                                                                                 │
│  signin.html                    index.html                                                      │
│  ┌────────────────────────┐     ┌───────────────────────────────────────────────────────────┐  │
│  │  "Sign in with Okta"   │     │  GET /api/me       → populate user name / email           │  │
│  │  button → /auth/start  │     │  POST /api/chat    → consume SSE event stream             │  │
│  │                        │     │  auth flow panel   → render interaction_required link      │  │
│  └────────────────────────┘     │  /api/chat/clear   → reset conversation history           │  │
│                                  └───────────────────────────────────────────────────────────┘  │
└────────────────────────┬──────────────────────────────┬────────────────────────────────────────┘
                         │  HTTPS (session cookie)       │  SSE stream (text/event-stream)
                         │  ①  ②                        │  ⑥  status · text · tool · token_meta ·
                         ▼                               │      done · error · interaction_required
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND  (Python / Uvicorn)                                                            │
│                                                                                                 │
│  main.py                                                                                        │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │  GET  /auth/start    → redirect browser to Okta /v1/authorize               ──────────① │   │
│  │  GET  /auth/callback ← receive auth code, POST Okta /v1/token               ──────────② │   │
│  │                        store {id_token, user{sub, email, name}} in session               │   │
│  │  GET  /auth/logout   → clear session + STS cache, redirect to Okta end_session          │   │
│  │  GET  /api/me        → return user from session (or 401)                                 │   │
│  │  POST /api/chat      → stream run_agent(message, id_token, sub, history)     ──────────⑥ │   │
│  │  POST /api/chat/clear→ drop in-memory history for user sub                              │   │
│  └──────────────────────────────────────────┬──────────────────────────────────────────────┘   │
│                                             │                                                   │
│  agent.py  (agentic SSE loop)               │                                                   │
│  ┌──────────────────────────────────────────▼──────────────────────────────────────────────┐   │
│  │  1. yield status "Authenticating with Slack..."                                         │   │
│  │  2. call exchange_id_token_for_slack_token(id_token, cache_key=sub)        ──────────③  │   │
│  │     → yield token_meta{step=sts, expires_in, cached, token_prefix}                     │   │
│  │     → on interaction_required: yield interaction_required{uri} and return              │   │
│  │  3. open streamablehttp_client → mcp.slack.com/mcp  Bearer <xoxp-token>    ──────────④  │   │
│  │     mcp.initialize() + list_tools()  (~24 tools)                                       │   │
│  │     yield status "Ready (N tools available)"                                           │   │
│  │     yield token_meta{step=mcp, tool_count, tools[]}                                    │   │
│  │  4. agentic loop (max 10 iterations):                                                  │   │
│  │       AsyncAnthropic.messages.stream(claude-opus-4-7, tools, history)       ──────────⑤  │   │
│  │       yield text events while streaming                                                │   │
│  │       on tool_use: yield tool{name, input}                                             │   │
│  │                    mcp.call_tool(name, input) → append tool_result                     │   │
│  │       break when stop_reason ≠ tool_use                                               │   │
│  │  5. yield done{}                                                                       │   │
│  └─────────────────────────┬───────────────────────────────────────────────────────────---┘   │
│                             │                                                                   │
│  auth/okta_sts.py                                                                               │
│  ┌─────────────────────────▼───────────────────────────────────────────────────────────────┐   │
│  │  - Build client_assertion JWT  (RS256, OKTA_AGENT_PRIVATE_JWK, TTL=60s)                │   │
│  │      iss/sub = OKTA_AGENT_CLIENT_ID   aud = okta_token_url                             │   │
│  │  - POST {OKTA_DOMAIN}/v1/token   grant=token-exchange (RFC 8693)           ──────────③  │   │
│  │      subject_token      = user id_token                                                │   │
│  │      subject_token_type = urn:ietf:params:oauth:token-type:id_token                   │   │
│  │      requested_token_type = urn:okta:params:oauth:token-type:oauth-sts                │   │
│  │      client_assertion   = signed JWT above                                             │   │
│  │      resource           = OKTA_MCP_RESOURCE_INDICATOR (ORN)                           │   │
│  │  - Returns:  success → {access_token xoxp-..., expires_in}                            │   │
│  │              interaction_required → {interaction_uri}  (user consent needed)          │   │
│  │  - In-memory cache keyed by sub  (TTL = expires_in − 60s)                             │   │
│  └─────────────────────────────────────────────────────────────────────────────────────---┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
         │  ①②                    │  ③                      │  ④                  │  ⑤
         ▼                         ▼                          ▼                     ▼
┌─────────────────┐   ┌─────────────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  OKTA           │   │  OKTA FOR AI AGENTS      │   │  SLACK MCP       │   │  ANTHROPIC API   │
│  (OIDC)         │   │  (STS / token exchange)  │   │  SERVER          │   │                  │
│                 │   │                          │   │                  │   │  claude-opus-4-7 │
│  /v1/authorize  │   │  /v1/token               │   │  mcp.slack.com   │   │  messages.stream │
│  /v1/token      │   │  RFC 8693                │   │  /mcp            │   │  (tool use,      │
│  /v1/logout     │   │                          │   │  Streamable HTTP │   │   streaming,     │
│                 │   │  Validates:              │   │  ~24 tools       │   │   max_tokens=    │
│  Agentic App    │   │  • user id_token         │   │                  │   │   4096)          │
│  (OIDC Web App) │   │  • client_assertion JWT  │   │  Auth: Bearer    │   │                  │
│  issues:        │   │  • Managed Connection    │   │  <xoxp-token>    │   │  max_iterations  │
│  • id_token     │   │    (AI Agent → MCP Srv)  │   │                  │   │  = 10            │
│  • auth code    │   │                          │   └────────┬─────────┘   └──────────────────┘
└─────────────────┘   │  Returns:                │            │  ⑦ Slack API
                      │  • xoxp-... access_token │            ▼
                      │  • or interaction_uri     │   ┌──────────────────┐
                      │                          │   │  SLACK WORKSPACE │
                      │  Managed Connection:      │   │                  │
                      │  AI Agent ──► MCP Server  │   │  channels        │
                      │  (holds Slack app         │   │  messages        │
                      │   client_id + secret      │   │  users           │
                      │   + xoxp scopes)          │   │  DMs             │
                      └─────────────────────────--┘   └──────────────────┘


KEY FLOWS
─────────
① Sign-in:     Browser → /auth/start → Okta /v1/authorize (OIDC code flow)
               → /auth/callback → POST Okta /v1/token (code → id_token)
               → store {id_token, sub, email, name} in encrypted session cookie → /

② Per-request token exchange (RFC 8693):
               Browser POST /api/chat → FastAPI reads id_token from session
               → okta_sts.py builds client_assertion JWT (RS256, agent private key)
               → POST Okta /v1/token: id_token + client_assertion + resource ORN
               → Okta validates user identity + AI Agent identity + Managed Connection policy
               → Okta brokers Slack OAuth on user's behalf → returns xoxp-... token
               → token cached in memory by sub (TTL = expires_in − 60s)

③ interaction_required:
               On first login Okta may return interaction_uri (user consent not yet granted)
               → agent yields interaction_required SSE event → UI shows consent link
               → after consent, next request succeeds and token is cached

④ MCP connection:
               agent.py opens Streamable HTTP session to mcp.slack.com/mcp
               with Authorization: Bearer <xoxp-token>
               mcp.initialize() + list_tools() → ~24 Slack tools exposed to Claude

⑤ Agentic loop: Claude receives user message + conversation history + tool definitions
               Claude streams text and/or emits tool_use blocks
               agent.py dispatches each tool_use to mcp.call_tool() → Slack API result
               loop continues (max 10 iterations) until stop_reason ≠ tool_use

⑥ SSE stream:  All events yielded back to browser:
               status (progress), text (Claude response), tool (tool invocations),
               token_meta (STS step, MCP step), done, error, interaction_required

⑦ Slack API:   Slack MCP Server translates MCP tool calls into Slack REST API requests
               → reads/writes channels, messages, users, DMs in the Slack workspace
```
