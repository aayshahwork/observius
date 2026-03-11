from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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


settings = Settings()
