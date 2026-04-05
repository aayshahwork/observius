from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    ENVIRONMENT: str = "development"
    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/computeruse"
    ANTHROPIC_API_KEY: str = ""
    BROWSERBASE_API_KEY: str = ""
    BROWSERBASE_PROJECT_ID: str = ""
    R2_ACCESS_KEY: str = ""
    R2_SECRET_KEY: str = ""
    R2_BUCKET_NAME: str = "computeruse-recordings"
    R2_ENDPOINT: str = ""
    TWOCAPTCHA_API_KEY: str = ""
    ENCRYPTION_MASTER_KEY: str = "change-me"
    CANARY_DEPLOYMENT: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


worker_settings = WorkerSettings()


def is_r2_configured() -> bool:
    """Return True if R2 credentials and endpoint are present and non-placeholder."""
    key = worker_settings.R2_ACCESS_KEY
    secret = worker_settings.R2_SECRET_KEY
    endpoint = worker_settings.R2_ENDPOINT
    if not (key and secret and endpoint):
        return False
    placeholders = {"your_r2_access_key", "your_r2_secret_key", "your_r2_endpoint", "xxx"}
    if key in placeholders or secret in placeholders:
        return False
    # Cloudflare R2 endpoints must have a valid account ID (32-char hex)
    if "xxx" in endpoint or "your_" in endpoint or "ACCOUNT_ID" in endpoint:
        return False
    return True
