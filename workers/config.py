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
