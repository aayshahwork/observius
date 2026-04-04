import logging
import secrets

from pydantic import model_validator
from pydantic_settings import BaseSettings

_config_logger = logging.getLogger("pokant.config")


class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/computeruse"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # Browserbase
    BROWSERBASE_API_KEY: str = ""
    BROWSERBASE_PROJECT_ID: str = ""

    # Cloudflare R2
    R2_ACCESS_KEY: str = ""
    R2_SECRET_KEY: str = ""
    R2_BUCKET_NAME: str = "computeruse-recordings"
    R2_ENDPOINT: str = ""

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_IDS: dict[str, str] = {
        "startup": "",
        "growth": "",
        "enterprise": "",
    }

    # Security
    API_SECRET_KEY: str = "change-me"
    ENCRYPTION_MASTER_KEY: str = "change-me"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _replace_insecure_defaults(self) -> "Settings":
        """Auto-generate secure keys in dev so first-time setup doesn't break."""
        if self.API_SECRET_KEY == "change-me":
            self.API_SECRET_KEY = secrets.token_urlsafe(32)
            _config_logger.warning(
                "API_SECRET_KEY was 'change-me' — generated a random key for this session. "
                "Set a permanent value in .env for production."
            )
        if self.ENCRYPTION_MASTER_KEY == "change-me":
            self.ENCRYPTION_MASTER_KEY = secrets.token_hex(32)
            _config_logger.warning(
                "ENCRYPTION_MASTER_KEY was 'change-me' — generated a random key for this session. "
                "Set a permanent 64-char hex value in .env for production."
            )
        return self


settings = Settings()
