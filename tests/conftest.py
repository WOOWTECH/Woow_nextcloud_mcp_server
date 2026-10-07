from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest

from nextcloud_mcp_server.client import NextcloudClient, reset_auth_latch
from nextcloud_mcp_server.settings import Settings
from nextcloud_mcp_server.tools import NextcloudTools

sys.path.insert(0, str(Path(__file__).parent))

from fake_nextcloud import BASE_URL, LOGIN, PASSWORD, FakeNextcloud


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No NEXTCLOUD_MCP_* variables, no .env file and no backend_policy module leak in."""
    for key in list(os.environ):
        if key.upper().startswith("NEXTCLOUD_MCP_") and not key.upper().startswith(
            "NEXTCLOUD_MCP_IT_"
        ):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "backend_policy", raising=False)


@pytest.fixture(autouse=True)
def _fresh_auth_latch() -> Iterator[None]:
    """The 401/429 latch is process-wide; every test starts (and ends) without it."""
    reset_auth_latch()
    yield
    reset_auth_latch()


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "base_url": BASE_URL,
            "username": LOGIN,
            "app_password": PASSWORD,
        }
        values.update(overrides)
        return Settings(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(readonly=False, allow_delete=True)


@pytest.fixture
def fake() -> FakeNextcloud:
    nc = FakeNextcloud()
    nc.add_folder("Docs")
    nc.add_file("Docs/readme.md", b"# Hello\n", "text/markdown")
    nc.add_file("Docs/a #1.md", b"hash name", "text/markdown")
    nc.add_folder("Docs/Sub")
    nc.add_file("Docs/Sub/deep.txt", b"deep", "text/plain")
    nc.add_file("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00", "image/png")
    nc.add_folder("Empty")
    return nc


@pytest.fixture
async def client(fake: FakeNextcloud, settings: Settings) -> AsyncIterator[NextcloudClient]:
    nc = NextcloudClient(settings, transport=fake.transport)
    yield nc
    await nc.aclose()


@pytest.fixture
def tools(client: NextcloudClient, settings: Settings) -> NextcloudTools:
    return NextcloudTools(client, settings)


def make_tools(fake: FakeNextcloud, settings: Settings) -> NextcloudTools:
    return NextcloudTools(NextcloudClient(settings, transport=fake.transport), settings)
