from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv as _load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Resolve .env search paths.
#
# When running from a repo checkout the layout is:
#   sdk/computeruse/config.py  →  sdk/  →  repo root
# When pip-installed, __file__ lives in site-packages and those traversals
# are meaningless, so we also search CWD.

_PACKAGE_DIR = Path(__file__).parent          # computeruse/
_SDK_DIR = _PACKAGE_DIR.parent                # sdk/ (or site-packages/)
_REPO_ROOT = _SDK_DIR.parent                  # repo root (or meaningless)
_CWD = Path.cwd()

# Candidate .env locations, checked in order (last loaded wins via override).
_ENV_CANDIDATES = [
    _REPO_ROOT / ".env",    # repo root  (dev checkout)
    _SDK_DIR / ".env",      # sdk/.env   (dev checkout)
    _CWD / ".env",          # current working directory (pip-installed / Docker)
]

# Force-load .env files before pydantic-settings reads env vars.
# override=True ensures .env values win over any ambient ANTHROPIC_API_KEY
# already set in the process (e.g. by Claude Code's subprocess runner).
# These are no-ops if the files don't exist.
for _env_path in _ENV_CANDIDATES:
    _load_dotenv(_env_path, override=True)


class Settings(BaseSettings):
    """Application-wide configuration loaded from environment variables or a .env file.

    Fields without a default are **required** — Pydantic raises ``ValidationError``
    at import time if they are missing or set to a placeholder value.  All other
    fields fall back to sensible defaults suitable for local development.

    Configuration sources (highest → lowest priority):

    1. Environment variables (e.g. ``export ANTHROPIC_API_KEY=sk-ant-…``)
    2. ``.env`` file in the current working directory
    3. Field defaults defined below

    Quick-start::

        # .env
        ANTHROPIC_API_KEY=sk-ant-...

        # Python
        from computeruse.config import settings
        print(settings.DEFAULT_MODEL)   # "claude-sonnet-4-5"
        print(settings.s3_configured)   # False (no AWS creds set)

    Never instantiate ``Settings`` directly in application code — import the
    module-level ``settings`` singleton instead so ``.env`` is parsed only once.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _SDK_DIR / ".env", _CWD / ".env"),
        env_file_encoding="utf-8",
        # Variable names are matched case-sensitively so ANTHROPIC_API_KEY and
        # anthropic_api_key are treated as different variables.
        case_sensitive=True,
        # Unknown env vars are silently ignored rather than raising.
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Required                                                             #
    # ------------------------------------------------------------------ #

    ANTHROPIC_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "Anthropic API key used for all model calls. " "Obtain yours at https://console.anthropic.com/settings/keys"
        ),
    )

    # ------------------------------------------------------------------ #
    # Optional — cloud / storage services                                 #
    # ------------------------------------------------------------------ #

    BROWSERBASE_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "BrowserBase API key for managed cloud browser sessions. "
            "Required when passing use_cloud=True to BrowserManager or "
            "ComputerUse(local=False)."
        ),
    )
    OPENAI_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "OpenAI API key for optional model fallback. "
            "Not used by the SDK directly — available for custom executor subclasses."
        ),
    )
    AWS_ACCESS_KEY_ID: Optional[str] = Field(
        default=None,
        description="AWS access key ID for S3 replay and screenshot storage",
    )
    AWS_SECRET_ACCESS_KEY: Optional[str] = Field(
        default=None,
        description="AWS secret access key paired with AWS_ACCESS_KEY_ID",
    )
    AWS_BUCKET_NAME: str = Field(
        default="computeruse-replays",
        description="S3 bucket name where session replays and screenshots are uploaded",
    )
    AWS_REGION: str = Field(
        default="us-east-1",
        description="AWS region that hosts the S3 bucket (e.g. 'us-west-2', 'eu-central-1')",
    )

    # ------------------------------------------------------------------ #
    # Optional — Pokant API reporting                                   #
    # ------------------------------------------------------------------ #

    POKANT_API_URL: Optional[str] = Field(
        default=None,
        description=(
            "Base URL of the Pokant API for automatic run reporting. "
            "When set alongside POKANT_API_KEY, local SDK runs are "
            "automatically ingested into the dashboard. "
            "Example: 'http://localhost:8000' or 'https://api.pokant.dev'"
        ),
    )
    POKANT_API_KEY: Optional[str] = Field(
        default=None,
        description=(
            "API key for authenticating with the Pokant ingest endpoint. "
            "Required alongside POKANT_API_URL for automatic reporting."
        ),
    )

    # ------------------------------------------------------------------ #
    # Optional — backend services (cloud API mode only)                   #
    # ------------------------------------------------------------------ #

    DATABASE_URL: str = Field(
        default="postgresql://localhost/computeruse",
        description=(
            "asyncpg-compatible PostgreSQL DSN used by the cloud API backend. " "Not required for local SDK usage."
        ),
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description=(
            "Redis connection URL used as the Celery broker and result backend. " "Not required for local SDK usage."
        ),
    )

    # ------------------------------------------------------------------ #
    # Execution defaults (overridable per-task via TaskConfig)            #
    # ------------------------------------------------------------------ #

    DEFAULT_MODEL: str = Field(
        default="claude-sonnet-4-5",
        description=(
            "Anthropic model ID used when none is specified per-task. "
            "Valid IDs: 'claude-sonnet-4-5', 'claude-opus-4-5', 'claude-haiku-4-5-20251001'. "
            "See https://docs.anthropic.com/en/docs/about-claude/models for the current list."
        ),
    )
    DEFAULT_TIMEOUT: int = Field(
        default=300,
        ge=1,
        description=(
            "Default wall-clock timeout in seconds for a single task run. "
            "Can be overridden per-task via TaskConfig.timeout_seconds."
        ),
    )
    DEFAULT_MAX_STEPS: int = Field(
        default=50,
        ge=1,
        description=(
            "Default maximum number of browser actions allowed per task run. "
            "Can be overridden per-task via TaskConfig.max_steps."
        ),
    )

    # ------------------------------------------------------------------ #
    # Local storage paths                                                  #
    # ------------------------------------------------------------------ #

    SESSION_DIR: str = Field(
        default="./sessions",
        description=(
            "Directory where browser session state (cookies, localStorage) is "
            "persisted between runs. Created automatically if it does not exist."
        ),
    )
    REPLAY_DIR: str = Field(
        default="./replays",
        description=(
            "Directory where task replay JSON files and step screenshots are written. "
            "Created automatically if it does not exist."
        ),
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("ANTHROPIC_API_KEY")
    @classmethod
    def anthropic_key_must_not_be_placeholder(cls, v: Optional[str]) -> Optional[str]:
        """Reject placeholder values that slip through from .env.example.

        Catches common copy-paste mistakes where the developer copies
        ``.env.example`` but forgets to replace the placeholder strings.
        Returns ``None`` unchanged to support deferred validation (the key
        is checked at the first LLM call, not at import time).

        Args:
            v: The raw value of ``ANTHROPIC_API_KEY``.

        Returns:
            The key unchanged when it looks like a real value, or ``None``.

        Raises:
            ValueError: If the key is a known placeholder string.
        """
        if v is None:
            return v
        placeholders = {"your_key_here", "", "none", "null", "sk-ant-placeholder"}
        if v.strip().lower() in placeholders:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set to a real Anthropic API key. "
                "Current value looks like a placeholder. "
                "Get your key at https://console.anthropic.com/settings/keys"
            )
        return v

    @model_validator(mode="after")
    def warn_on_partial_aws_config(self) -> Settings:
        """Log a warning when only one of the two required AWS credentials is set.

        Having ``AWS_ACCESS_KEY_ID`` without ``AWS_SECRET_ACCESS_KEY`` (or vice
        versa) will cause S3 operations to fail at runtime.  Catching this
        mismatch at startup produces a clear, actionable message rather than a
        cryptic boto3 error mid-task.

        Returns:
            ``self`` unchanged (warnings are non-fatal).
        """
        has_key = bool(self.AWS_ACCESS_KEY_ID)
        has_secret = bool(self.AWS_SECRET_ACCESS_KEY)
        if has_key != has_secret:
            missing = "AWS_SECRET_ACCESS_KEY" if has_key else "AWS_ACCESS_KEY_ID"
            logger.warning(
                "Partial AWS credentials detected: %s is not set. "
                "S3 replay uploads will fail until both "
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are configured.",
                missing,
            )
        return self

    # ------------------------------------------------------------------ #
    # Convenience properties                                               #
    # ------------------------------------------------------------------ #

    @property
    def s3_configured(self) -> bool:
        """Return ``True`` when both AWS credentials and a bucket name are present.

        Use this guard before calling any function in ``backend/storage.py``
        so that missing credentials produce a clear early error rather than a
        ``botocore.exceptions.NoCredentialsError`` deep in the upload path.

        Example::

            if settings.s3_configured:
                url = await upload_replay(path, task_id)
            else:
                logger.info("S3 not configured — replay stored locally only")
        """
        return bool(self.AWS_ACCESS_KEY_ID and self.AWS_SECRET_ACCESS_KEY and self.AWS_BUCKET_NAME)

    @property
    def browserbase_configured(self) -> bool:
        """Return ``True`` when a BrowserBase API key is present.

        Example::

            browser = await manager.setup_browser(
                use_cloud=settings.browserbase_configured
            )
        """
        return bool(self.BROWSERBASE_API_KEY)


# ---------------------------------------------------------------------------
# Singleton — import this throughout the SDK instead of constructing Settings
# directly so that .env is read only once per process.
#
# Usage:
#   from computeruse.config import settings
#   print(settings.DEFAULT_MODEL)
# ---------------------------------------------------------------------------
settings = Settings()
