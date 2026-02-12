"""
Application settings loaded from environment variables.

Uses pydantic-settings for type-safe configuration with .env file support.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global application settings."""

    # App
    APP_NAME: str = "TradeFlow Africa"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change-me"
    FERNET_KEY: str = "ZGV2LWtleS1jaGFuZ2UtaW4tcHJvZHVjdGlvbi1wbHM="  # run Fernet.generate_key() for production

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://tradeflow:tradeflow_dev@localhost:5432/tradeflow"
    DATABASE_POOL_SIZE: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_SSL: bool = False

    # JWT
    JWT_PRIVATE_KEY_PATH: str = "keys/private.pem"
    JWT_PUBLIC_KEY_PATH: str = "keys/public.pem"
    JWT_ALGORITHM: str = "RS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # OTP
    OTP_LENGTH: int = 6
    OTP_EXPIRE_SECONDS: int = 300

    # WhatsApp
    WHATSAPP_API_URL: str = "https://graph.facebook.com/v18.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    WHATSAPP_APP_SECRET: str = ""  # Used for webhook signature validation

    # KYC / VerifyMe
    VERIFYME_BASE_URL: str = "https://vapi.verifyme.ng/v1"
    VERIFYME_API_KEY: str = ""
    VERIFYME_MOCK: bool = True  # set False in production to call real API
    BVN_API_URL: str = ""
    BVN_API_KEY: str = ""
    NIN_API_URL: str = ""
    NIN_API_KEY: str = ""

    # Providus Bank
    PROVIDUS_BASE_URL: str = ""
    PROVIDUS_CLIENT_ID: str = ""
    PROVIDUS_CLIENT_SECRET: str = ""
    PROVIDUS_ACCOUNT_NUMBER: str = ""
    PROVIDUS_WEBHOOK_SECRET: str = ""   # HMAC-SHA512 key for webhook signatures
    PAYMENT_EXPIRY_HOURS: int = 2       # Hours before INITIATED transactions expire

    # CIPS
    CIPS_API_URL: str = ""
    CIPS_API_KEY: str = ""
    CIPS_MERCHANT_ID: str = ""

    # FX Rates
    FX_RATE_PROVIDER: str = "exchangerate-api"
    FX_RATE_API_KEY: str = ""
    FX_RATE_MOCK: bool = True  # set False in production to call real API
    FX_RATE_CACHE_TTL_SECONDS: int = 60
    FX_QUOTE_TTL_SECONDS: int = 60

    # Matching Engine
    MATCHING_CYCLE_INTERVAL_SECONDS: int = 300
    MATCHING_TOLERANCE_PERCENT: float = 5.0
    MATCHING_POOL_TIMEOUT_HOURS: int = 24

    # SMS
    SMS_API_URL: str = ""
    SMS_API_KEY: str = ""
    SMS_SENDER_ID: str = "TradeFlow"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Sentry
    SENTRY_DSN: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
