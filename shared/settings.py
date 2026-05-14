# shared/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kalshi credentials
    demo_kalshi_api_key: str
    demo_kalshi_read_private_key_path: str
    demo_kalshi_write_private_key_path: str
    live_kalshi_api_key: str
    live_kalshi_read_private_key_path: str
    kalshi_env: str = "demo"  # "demo" | "live"

    # Postgres
    postgres_password: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_db: str = "crypto_analysis"

    # Coinbase CDP
    coinbase_cdp_key_name: str
    coinbase_cdp_private_key: str

    # Risk / ingestor
    risk_stale_market_seconds: int = 60

    # Training campaign
    training_campaign_enabled: bool = False
    training_campaign_lookback_hours: int = 24
    training_campaign_cooldown_seconds: int = 600
    training_campaign_max_recent_per_market: int = 5

    @property
    def kalshi_api_key(self) -> str:
        return self.demo_kalshi_api_key if self.kalshi_env == "demo" else self.live_kalshi_api_key

    @property
    def kalshi_private_key_path(self) -> str:
        return (
            self.demo_kalshi_read_private_key_path
            if self.kalshi_env == "demo"
            else self.live_kalshi_read_private_key_path
        )

    @property
    def kalshi_base_url(self) -> str:
        if self.kalshi_env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://trading-api.kalshi.com/trade-api/v2"

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
