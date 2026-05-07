"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the chap-scheduler service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CHAP_SCHEDULER_",
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1", description="Bind host for the FastAPI server.")
    port: int = Field(default=9090, description="Bind port for the FastAPI server.")
    log_level: str = Field(default="info", description="Uvicorn log level.")
    reload: bool = Field(default=False, description="Enable auto-reload (development).")

    embed_prefect: bool = Field(
        default=True,
        description="Mount the Prefect server (API + UI + background services) inside this app.",
    )
    prefect_mount_path: str = Field(
        default="/prefect",
        description="Path prefix at which the embedded Prefect app is mounted.",
    )

    prediction_timeout_seconds: int = Field(
        default=60 * 60,
        description=(
            "How long the worker waits while polling a chap prediction job before giving up. "
            "Note: this is wait-time on our side -- chap is fully async and keeps running "
            "regardless. Bumping past one hour usually points at a slow model rather than a "
            "scheduler-side issue."
        ),
    )


def get_settings() -> Settings:
    """Return a fresh Settings instance."""
    return Settings()
