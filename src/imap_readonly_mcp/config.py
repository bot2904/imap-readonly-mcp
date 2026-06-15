"""Configuration models for the read-only IMAP MCP server."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .exceptions import ConfigurationError


class AccountProtocol(StrEnum):
    """Supported email access protocols."""

    IMAP = "imap"


class ConnectorSecurityConfig(BaseModel):
    """Transport security toggles for the IMAP connector."""

    use_ssl: bool = Field(
        default=True, description="Whether to use implicit TLS from the beginning of the connection."
    )
    starttls: bool = Field(default=False, description="Whether to upgrade the connection with STARTTLS.")
    verify_ssl: bool = Field(
        default=True, description="If false, SSL certificate verification is disabled (not safe)."
    )


class MailAccountConfig(BaseModel):
    """Configuration for the single IMAP account exposed through the server."""

    protocol: AccountProtocol = Field(description="Protocol used to access this mailbox. Only 'imap' is supported.")
    description: str | None = Field(default=None, description="Human readable description of the account.")

    host: str | None = Field(default=None, description="IMAP server host.")
    port: int | None = Field(default=None, description="IMAP server port. Defaults to 993 with SSL or 143 without SSL.")
    username: str | None = Field(default=None, description="Username for authenticating to the IMAP server.")
    password: SecretStr | None = Field(default=None, description="Password for authenticating to the IMAP server.")
    security: ConnectorSecurityConfig = Field(
        default_factory=ConnectorSecurityConfig, description="Transport security options."
    )
    timeout_seconds: float = Field(
        default=30.0, ge=5.0, le=180.0, description="Socket timeout used by the connector."
    )
    allowed_folders: list[str] | None = Field(
        default=None,
        description="Optional allow-list of folders that can be accessed via the server.",
    )
    excluded_folders: list[str] | None = Field(
        default=None,
        description="Optional block-list of folders that will never be exposed to clients.",
    )

    @model_validator(mode="after")
    def _validate_imap_settings(self) -> MailAccountConfig:
        if self.protocol is not AccountProtocol.IMAP:
            raise ConfigurationError("Only IMAP accounts are supported")
        if not self.host:
            raise ConfigurationError("host is required for imap accounts")
        if not self.username:
            raise ConfigurationError("username is required for imap accounts")
        if not self.password:
            raise ConfigurationError("password must be provided for imap accounts to ensure read-only login")
        return self


class MailSettings(BaseSettings):
    """Top-level configuration container for the server."""

    model_config = SettingsConfigDict(
        env_prefix="MAIL_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    account: MailAccountConfig = Field(description="Single IMAP account exposed by the server.")
    default_search_limit: int = Field(
        default=50, gt=0, description="Default limit applied to message search results."
    )
    maximum_search_limit: int = Field(
        default=200, gt=0, description="Hard limit to protect accidental large searches."
    )
    connection_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Number of times the server will retry failed connector operations.",
    )
    cache_path: Path | None = Field(
        default=None,
        description="Optional path to an on-disk cache used for message bodies (defaults to email_cache.sqlite in the working directory).",
    )
    fetch_concurrency: int = Field(
        default=6,
        ge=1,
        le=32,
        description="Maximum number of parallel message fetch operations when enriching summaries.",
    )

    config_path: Path | None = Field(
        default=None,
        description="Resolved path used to load configuration (for diagnostics).",
        exclude=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Prefer environment/.env values over configuration-file values."""

        return env_settings, dotenv_settings, init_settings, file_secret_settings


def load_settings(config_path: Path | None = None, overrides: dict[str, Any] | None = None) -> MailSettings:
    """Load configuration from YAML/JSON on disk combined with environment overrides."""

    base_data: dict[str, Any] = {}
    resolved_path: Path | None = None
    missing_config_error: ConfigurationError | None = None
    if config_path:
        resolved_path = Path(config_path).expanduser().resolve()
        if not resolved_path.exists():
            missing_config_error = ConfigurationError(f"Configuration file not found: {resolved_path}")
        else:
            try:
                with resolved_path.open("r", encoding="utf-8") as handle:
                    base_data = yaml.safe_load(handle.read()) or {}
            except yaml.YAMLError as exc:
                raise ConfigurationError(f"Unable to parse configuration file {resolved_path}: {exc}") from exc
    if overrides:
        base_data.update(overrides)

    if "account" not in base_data:
        legacy_accounts = base_data.get("accounts")
        if legacy_accounts:
            if isinstance(legacy_accounts, list):
                if not legacy_accounts:
                    raise ConfigurationError(
                        "Legacy 'accounts' array is empty; provide at least one account."
                    )
                base_data["account"] = legacy_accounts[0]
            elif isinstance(legacy_accounts, dict):
                base_data["account"] = legacy_accounts
            else:
                raise ConfigurationError("Legacy 'accounts' must be a list or mapping.")
    base_data.pop("accounts", None)

    try:
        settings = MailSettings(**base_data)
    except (ConfigurationError, ValidationError) as exc:
        if missing_config_error and not _has_account_environment():
            raise missing_config_error from exc
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
    settings.config_path = resolved_path
    return settings


def _has_account_environment() -> bool:
    """Return true if the process environment appears to configure an account."""

    return "MAIL_ACCOUNT" in os.environ or any(key.startswith("MAIL_ACCOUNT__") for key in os.environ)
