from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_licenses", ROOT / "scripts/check_licenses.py")
assert _spec is not None and _spec.loader is not None
check_licenses = importlib.util.module_from_spec(_spec)
sys.modules["check_licenses"] = check_licenses
_spec.loader.exec_module(check_licenses)


@pytest.mark.parametrize(
    ("expression", "allowed"),
    [
        ("MIT", True),
        ("Apache-2.0 OR BSD-3-Clause", True),
        ("MIT OR GPL-3.0-only", True),
        ("MIT AND GPL-3.0-only", False),
        ("GPL-2.0-or-later", False),
        ("LGPL-3.0", False),
        ("AGPL-3.0-only", False),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", True),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("LicenseRef-Proprietary", False),
        ("MIT AND", False),
        ("", False),
    ],
)
def test_spdx_allowed(expression: str, allowed: bool) -> None:
    assert check_licenses.spdx_allowed(expression) is allowed


@pytest.mark.parametrize(
    ("meta", "allowed"),
    [
        ((None, ["License :: OSI Approved :: MIT License"], None), True),
        ((None, ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"], None), False),
        (
            (
                None,
                [
                    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
                    "License :: OSI Approved :: MIT License",
                ],
                None,
            ),
            True,
        ),
        ((None, [], "BSD License"), True),
        ((None, [], "MIT License\n\nPermission is hereby granted..."), True),
        ((None, [], "GNU GENERAL PUBLIC LICENSE Version 3"), False),
        ((None, [], "Some custom terms"), False),
        ((None, [], "Apache-2.0 OR MIT"), True),
        ((None, [], None), False),
        ((None, ["License :: Other/Proprietary License"], None), False),
    ],
)
def test_classify(meta: tuple, allowed: bool) -> None:
    assert check_licenses.classify(*meta)[1] is allowed


def test_runtime_closure_from_lock() -> None:
    names = {name for name, _ in check_licenses.runtime_closure(ROOT / "uv.lock")}
    assert {"fastmcp", "mcp", "httpx", "pydantic-settings", "defusedxml"} <= names
    assert "tzdata" in names  # Windows-only dependency is still checked
    assert not names & {"pytest", "ruff", "pytest-cov", "woow-nextcloud-mcp-server"}


def test_main_offline_on_installed_closure(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_licenses.main(["--offline", "--markdown"])
    out = capsys.readouterr().out
    assert out.startswith("| Package | Version | Licence |")
    assert "| fastmcp |" in out
    # packages for other platforms are not installed here, so offline mode fails on them
    assert code in (0, 1)
