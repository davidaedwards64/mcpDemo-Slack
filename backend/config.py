from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str
    session_secret: str = ""
    slack_mcp_token: str = ""  # Phase 1 fallback; not used when Okta is configured

    # Okta OIDC — web app (confidential client)
    okta_client_id: str = ""
    okta_client_secret: str = ""
    okta_domain: str = ""           # e.g. your-org.okta.com (no https://)
    okta_redirect_uri: str = "http://localhost:8000/auth/callback"

    # Okta AI Agent workload — private key JWT client
    okta_agent_client_id: str = ""
    okta_agent_private_jwk: str = ""  # RSA private key as single-line JSON string

    # ORN of the Slack MCP Server object registered in Okta
    okta_mcp_resource_indicator: str = ""

    @property
    def okta_issuer(self) -> str:
        """Base URL for the Okta org authorization server."""
        return f"https://{self.okta_domain}/oauth2" if self.okta_domain else ""

    @property
    def okta_token_url(self) -> str:
        """Org-level token endpoint used for STS token exchange."""
        return f"https://{self.okta_domain}/oauth2/v1/token" if self.okta_domain else ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
