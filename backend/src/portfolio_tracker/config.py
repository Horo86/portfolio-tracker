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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "app.sqlite"

    @property
    def pre_migration_backup_dir(self) -> Path:
        return self.data_dir / "backups" / "pre-migration"
