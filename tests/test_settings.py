from __future__ import annotations

from pathlib import Path

import pytest

from fake_nextcloud import PASSWORD
from nextcloud_mcp_server.settings import SettingsError, load_settings


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(f"NEXTCLOUD_MCP_{key}", value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, BASE_URL="https://cloud.example.com/", USERNAME="alice", APP_PASSWORD="pw")
    settings = load_settings()
    assert settings.base_url == "https://cloud.example.com"
    assert settings.username == "alice"
    assert settings.app_password.get_secret_value() == "pw"
    assert settings.readonly is True
    assert settings.allow_delete is False
    assert settings.disabled_tool_names == frozenset()
    assert settings.request_timeout == 30
    assert settings.max_text_bytes == 1_048_576
    assert settings.max_upload_bytes == 10_485_760
    assert settings.tree_max_entries == 500
    assert settings.verify_tls is True


def test_all_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        BASE_URL="http://192.168.1.5:8080/nextcloud//",
        USERNAME=" bob ",
        APP_PASSWORD="pw",
        READONLY="false",
        ALLOW_DELETE="true",
        DISABLED_TOOLS=" upload_file, ,list_tasks ",
        REQUEST_TIMEOUT="5.5",
        MAX_TEXT_BYTES="10",
        MAX_UPLOAD_BYTES="20",
        TREE_MAX_ENTRIES="3",
        VERIFY_TLS="false",
    )
    settings = load_settings()
    assert settings.base_url == "http://192.168.1.5:8080/nextcloud"
    assert settings.username == "bob"
    assert settings.readonly is False
    assert settings.allow_delete is True
    assert settings.disabled_tool_names == {"upload_file", "list_tasks"}
    assert settings.request_timeout == 5.5
    assert (settings.max_text_bytes, settings.max_upload_bytes) == (10, 20)
    assert settings.tree_max_entries == 3
    assert settings.verify_tls is False


def test_dotenv_file_and_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "NEXTCLOUD_MCP_BASE_URL=https://from-file.example.com\n"
        "NEXTCLOUD_MCP_USERNAME=file-user\n"
        "NEXTCLOUD_MCP_APP_PASSWORD=file-pw\n"
        "UNRELATED=1\n",
        encoding="utf-8",
    )
    _env(monkeypatch, USERNAME="env-user")
    settings = load_settings()
    assert settings.base_url == "https://from-file.example.com"
    assert settings.username == "env-user"


def test_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SettingsError) as info:
        load_settings()
    message = str(info.value)
    assert "NEXTCLOUD_MCP_BASE_URL is required" in message
    assert "NEXTCLOUD_MCP_USERNAME is required" in message
    assert "NEXTCLOUD_MCP_APP_PASSWORD is required" in message
    assert "\n" not in message


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("ftp://cloud.example.com", "https://"),
        ("cloud.example.com", "https://"),
        ("https://user:hunter2@cloud.example.com", "user name or password"),
        ("https://cloud.example.com/?x=1", "query or fragment"),
        ("https://cloud.example.com/#top", "query or fragment"),
        ("https://", "host name"),
        ("https://cloud.example.com:99999", "invalid port"),
        ("https://cloud example.com", "spaces"),
        ("   ", "must not be empty"),
    ],
)
def test_bad_base_url(monkeypatch: pytest.MonkeyPatch, url: str, fragment: str) -> None:
    _env(monkeypatch, BASE_URL=url, USERNAME="alice", APP_PASSWORD=PASSWORD)
    with pytest.raises(SettingsError) as info:
        load_settings()
    assert fragment in str(info.value)
    assert "hunter2" not in str(info.value)
    assert PASSWORD not in str(info.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("USERNAME", " "),
        ("USERNAME", "a:b"),
        ("APP_PASSWORD", " "),
        ("APP_PASSWORD", "abc\ndef"),
        ("REQUEST_TIMEOUT", "0"),
        ("MAX_TEXT_BYTES", "0"),
        ("TREE_MAX_ENTRIES", "-1"),
        ("READONLY", "maybe"),
    ],
)
def test_bad_values_never_echo_input(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    _env(monkeypatch, BASE_URL="https://c.example.com", USERNAME="alice", APP_PASSWORD="pw-xyz")
    _env(monkeypatch, **{key: value})
    with pytest.raises(SettingsError) as info:
        load_settings()
    message = str(info.value)
    assert f"NEXTCLOUD_MCP_{key}" in message
    assert "pw-xyz" not in message
    if key == "APP_PASSWORD":
        assert "abc" not in message
