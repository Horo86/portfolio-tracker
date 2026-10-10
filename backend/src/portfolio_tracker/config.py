"""Configuration from environment variables (prefix PT_)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PT_")

    data_dir: Path = Path("/data")
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    # Built frontend (index.html + assets). Not served when unset or missing.
    static_dir: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "app.sqlite"

    @property
    def pre_migration_backup_dir(self) -> Path:
        return self.data_dir / "backups" / "pre-migration"
