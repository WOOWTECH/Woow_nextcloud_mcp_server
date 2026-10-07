"""Runtime settings, read from ``NEXTCLOUD_MCP_*`` environment variables (and ``.env``)."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "NEXTCLOUD_MCP_"


class SettingsError(Exception):
    """Start-up configuration problem. The message never contains a secret value."""


class Settings(BaseSettings):
    """Settings of one server process (one Nextcloud account)."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    base_url: str = Field(description="Nextcloud root URL.")
    username: str = Field(description="Login name of the account.")
    app_password: SecretStr = Field(description="App password of the account.")
    readonly: bool = True
    allow_delete: bool = False
    disabled_tools: str = ""
    request_timeout: float = Field(default=30.0, gt=0, le=3600)
    max_text_bytes: int = Field(default=1_048_576, ge=1)
    max_upload_bytes: int = Field(default=10_485_760, ge=1)
    tree_max_entries: int = Field(default=500, ge=1)
    verify_tls: bool = True

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in value):
            raise ValueError("must not contain spaces or control characters")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https"):
            raise ValueError("must start with https:// or http://")
        if "@" in parts.netloc:
            raise ValueError("must not contain a user name or password")
        if "?" in value or "#" in value:
            raise ValueError("must not contain a query or fragment")
        if not parts.hostname:
            raise ValueError("must contain a host name")
        try:
            _ = parts.port
        except ValueError:
            raise ValueError("has an invalid port") from None
        path = parts.path.rstrip("/")
        return f"{parts.scheme}://{parts.netloc}{path}"

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or ":" in value:
            raise ValueError("must not contain control characters or ':'")
        return value

    @field_validator("app_password")
    @classmethod
    def _check_password(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip():
            raise ValueError("must not be empty")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
            raise ValueError("must not contain control characters")
        return value

    @property
    def disabled_tool_names(self) -> frozenset[str]:
        """Names from ``DISABLED_TOOLS`` (comma separated, blanks ignored)."""
        return frozenset(part.strip() for part in self.disabled_tools.split(",") if part.strip())


def _describe(error: ValidationError) -> str:
    """One line describing the first problem, naming variables but never values."""
    problems = []
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        field = str(item["loc"][0]).upper() if item.get("loc") else "settings"
        if item.get("type") == "missing":
            problems.append(f"{ENV_PREFIX}{field} is required")
        else:
            message = str(item.get("msg", "is invalid"))
            message = message.removeprefix("Value error, ")
            problems.append(f"{ENV_PREFIX}{field}: {message}")
    return "; ".join(problems) or "invalid settings"


def load_settings(**overrides: object) -> Settings:
    """Load settings from the environment, raising :class:`SettingsError` on problems."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise SettingsError(_describe(exc)) from None
