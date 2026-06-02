"""Obtain a Slack MCP OAuth user token via browser-based authorization code + PKCE.

Run once to populate SLACK_MCP_TOKEN (and SLACK_REFRESH_TOKEN) in .env.
Re-run if the token expires.

Prerequisites:
  - SLACK_CLIENT_ID and SLACK_CLIENT_SECRET set in .env
  - http://localhost:8888/callback added as a redirect URI in your Slack app
  - User token scopes added to your Slack app (see README)
"""

import asyncio
import base64
import hashlib
import os
import re
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import httpx
from dotenv import load_dotenv

load_dotenv()

AUTHORIZE_URL = "https://slack.com/oauth/v2_user/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.user.access"
REDIRECT_URI = "http://localhost:8888/callback"
SCOPES = " ".join([
    "channels:read", "channels:history",
    "groups:read", "groups:history",
    "mpim:read", "mpim:history",
    "im:read", "im:history",
    "users:read", "users:read.email",
    "chat:write",
    "search:read.public", "search:read.private",
    "search:read.mpim", "search:read.im",
    "reactions:read", "reactions:write",
    "files:read", "emoji:read",
    "canvases:read",
])

_auth_code: str | None = None
_auth_error: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code, _auth_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _auth_code = params["code"][0]
        elif "error" in params:
            _auth_error = params["error"][0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h2>Auth complete \xe2\x80\x94 you can close this tab.</h2></body></html>")

    def log_message(self, format, *args):
        pass  # suppress request logging


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _save_env(key: str, value: str, env_path: Path) -> None:
    text = env_path.read_text()
    if re.search(rf"^{key}=", text, re.MULTILINE):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    env_path.write_text(text)


async def main() -> None:
    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("ERROR: Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env first.")
        return

    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)

    auth_params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTHORIZE_URL}?{auth_params}"

    server = HTTPServer(("localhost", 8888), _CallbackHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()

    print("Opening browser for Slack authorization...")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    while _auth_code is None and _auth_error is None:
        await asyncio.sleep(0.3)
    server.shutdown()

    if _auth_error:
        print(f"ERROR: Slack returned error: {_auth_error}")
        return

    print("Authorization code received, exchanging for token...")
    async with httpx.AsyncClient() as http:
        resp = await http.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        })

    body = resp.json()
    if not body.get("ok"):
        print(f"ERROR: Token exchange failed — {body.get('error', body)}")
        return

    # Token may be top-level or nested under authed_user depending on app type
    authed = body.get("authed_user", {})
    access_token = body.get("access_token") or authed.get("access_token", "")
    refresh_token = body.get("refresh_token") or authed.get("refresh_token", "")

    if not access_token:
        print(f"ERROR: No access_token in response: {body}")
        return

    env_path = Path(__file__).parent.parent / ".env"
    _save_env("SLACK_MCP_TOKEN", access_token, env_path)
    print(f"Saved SLACK_MCP_TOKEN to .env  (prefix: {access_token[:20]}...)")

    if refresh_token:
        _save_env("SLACK_REFRESH_TOKEN", refresh_token, env_path)
        print("Saved SLACK_REFRESH_TOKEN to .env")

    print("\nRun the smoke test: python3 scripts/smoke_test.py")


if __name__ == "__main__":
    asyncio.run(main())
