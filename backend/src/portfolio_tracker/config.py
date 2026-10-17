"""Configuration from environment variables (prefix PT_)."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PT_")

    data_dir: Path = Path("/data")
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    # Built frontend (index.html + assets). Not served when unset or missing.
    static_dir: Path | None = None
    # Session cookie `Secure` flag. Browsers drop Secure cookies on plain http, except on
    # localhost: set to false only when the app is reached over http on a trusted LAN.
    cookie_secure: bool = True
    # Session expiry: after this many days without use, or this many days after login.
    session_idle_days: int = Field(default=7, ge=1)
    session_absolute_days: int = Field(default=30, ge=1)
    # Market data engines (D-123, D-132): **initial values only**, copied into the database
    # at the first start; afterwards the database wins. Comma-separated engine names in
    # order, checked against the registered adapters and the roles they serve.
    # Engines for ETFs and stocks (empty = manual prices only).
    price_providers: str = "yahoo"
    # Engines for bonds (empty = manual prices for bonds).
    bond_price_providers: str = ""
    # Engines for exchange rates.
    fx_providers: str = "frankfurter"
    # Preferred exchanges for the listing of an instrument, in order (D-125): provider
    # exchange codes or names, comma-separated, compared ignoring case. Empty: the engine's
    # first listing in the instrument's currency. Also an initial value.
    price_exchanges: str = ""
    # Frankfurter (ECB reference rates) base URL; may point to a self-hosted copy.
    fx_url: str = "https://api.frankfurter.dev"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "app.sqlite"

    @property
    def pre_migration_backup_dir(self) -> Path:
        return self.data_dir / "backups" / "pre-migration"
