"""Runtime configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from the environment.

    Every field can be overridden with a ``GEO_`` environment variable, for
    example ``GEO_MAX_UPLOAD_BYTES``.
    """

    model_config = SettingsConfigDict(
        env_prefix="GEO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/measurements.db"
    upload_dir: Path = Path("./data/uploads")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_unzipped_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_zip_files: int = Field(default=100, gt=0)
    max_features: int = Field(default=50_000, gt=0)
    # Densify geographic edges so a projected measurement follows the ellipsoid.
    segment_length_degrees: float = Field(default=0.05, gt=0, le=1)

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        database_path = self.sqlite_path()
        if database_path is not None:
            database_path.parent.mkdir(parents=True, exist_ok=True)

    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw = self.database_url[len(prefix) :]
        if raw in {":memory:", ""} or raw.startswith("file:"):
            return None
        return Path(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
