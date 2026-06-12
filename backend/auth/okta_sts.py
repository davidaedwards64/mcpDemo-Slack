"""Okta STS token exchange — trades a user id_token for a Slack access token.

Uses RFC 8693 token exchange with requested_token_type=oauth-sts.
Handles the interaction_required case where the user must consent to Slack access.
"""

import json
import logging
import time
import uuid
from typing import Any

import httpx
from jose import jwt

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Simple in-memory cache: cache_key → {"data": dict, "expires_at": float}
_cache: dict[str, dict[str, Any]] = {}


def clear_cached_token(cache_key: str) -> None:
    """Remove a user's cached Slack token, forcing a fresh STS exchange on next request."""
    _cache.pop(cache_key, None)


async def revoke_user_grants(user_sub: str) -> None:
    """Delete all Okta grants for the user via the Management API.

    Targets DELETE /api/v1/users/{userId}/grants (all grants) rather than
    scoping to a specific client ID, because the AI Agents STS consent grant
    may not be stored under the agent client ID.
    Silently skips if OKTA_API_TOKEN is not configured.
    """
    settings = get_settings()
    if not settings.okta_api_token or not settings.okta_domain:
        logger.warning("Grant revocation skipped: OKTA_API_TOKEN or OKTA_DOMAIN not configured")
        return

    url = f"https://{settings.okta_domain}/api/v1/users/{user_sub}/grants"
    logger.info("Revoking all Okta grants for user %s via %s", user_sub, url)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                url,
                headers={"Authorization": f"SSWS {settings.okta_api_token}"},
            )
        if resp.is_success:
            logger.info("Revoked all Okta grants for user %s (status %s)", user_sub, resp.status_code)
        elif resp.status_code == 404:
            logger.info("No Okta grants found for user %s (404)", user_sub)
        else:
            logger.warning(
                "Failed to revoke Okta grants for user %s: HTTP %s — %s",
                user_sub, resp.status_code, resp.text,
            )
    except Exception:
        logger.exception("Error revoking Okta grants for user %s", user_sub)


def create_client_assertion_jwt(client_id: str, private_jwk_str: str, token_url: str) -> str:
    """Sign a short-lived RS256 JWT for use as client_assertion in the token exchange."""
    private_jwk = json.loads(private_jwk_str)
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    headers: dict[str, str] = {"alg": "RS256"}
    if "kid" in private_jwk:
        headers["kid"] = private_jwk["kid"]
    return jwt.encode(claims, private_jwk, algorithm="RS256", headers=headers)


async def exchange_id_token_for_slack_token(
    user_id_token: str | None,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """Exchange a user id_token for a Slack access token via Okta STS.

    Returns a dict with one of these shapes:

    Success:
        {"status": "success", "access_token": str, "expires_in": int, "cached": bool}

    Interaction required (user must consent via Okta):
        {"status": "interaction_required", "interaction_uri": str}

    Not configured / missing input:
        {"status": "not_configured" | "no_token", "error": str}

    Exchange failed / unexpected error:
        {"status": "exchange_failed" | "error", "error": str}
    """
    settings = get_settings()

    if not settings.okta_agent_client_id or not settings.okta_agent_private_jwk:
        return {
            "status": "not_configured",
            "error": "OKTA_AGENT_CLIENT_ID or OKTA_AGENT_PRIVATE_JWK not set",
        }

    if not user_id_token:
        return {"status": "no_token", "error": "No user id_token provided"}

    # Return cached entry if still valid
    if cache_key and cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() < entry["expires_at"]:
            result = dict(entry["data"])
            result["cached"] = True
            return result

    try:
        token_url = settings.okta_token_url
        client_assertion = create_client_assertion_jwt(
            settings.okta_agent_client_id,
            settings.okta_agent_private_jwk,
            token_url,
        )
        payload: dict[str, str] = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_id_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
            "requested_token_type": "urn:okta:params:oauth:token-type:oauth-sts",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": client_assertion,
        }
        if settings.okta_mcp_resource_indicator:
            payload["resource"] = settings.okta_mcp_resource_indicator

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                token_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        body = resp.json()

        # Okta returns 400 for interaction_required
        if resp.status_code != 200:
            if body.get("error") == "interaction_required":
                interaction_uri = body.get("interaction_uri", "")
                return {
                    "status": "interaction_required",
                    "interaction_uri": interaction_uri,
                }
            logger.warning("Okta STS exchange failed (%s): %s", resp.status_code, resp.text)
            return {
                "status": "exchange_failed",
                "error": f"HTTP {resp.status_code}: {resp.text}",
            }

        access_token = body.get("access_token", "")
        expires_in = int(body.get("expires_in", 3600))

        result: dict[str, Any] = {
            "status": "success",
            "access_token": access_token,
            "expires_in": expires_in,
            "cached": False,
        }

        if cache_key:
            _cache[cache_key] = {
                "data": result,
                "expires_at": time.time() + expires_in - 60,  # 60s buffer
            }

        return result

    except Exception as exc:
        logger.exception("Unexpected error in Okta STS exchange")
        return {"status": "error", "error": str(exc)}
