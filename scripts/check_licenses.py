#!/usr/bin/env python3
"""Fail when the runtime dependency closure contains a non-permissive or unknown licence.

The closure is read from ``uv.lock``: every package reachable from the project's
runtime dependencies (extras followed, dev group excluded), for *all* platforms.
Licence data comes from the installed distribution metadata; packages that are not
installed here (other platforms) are looked up on PyPI unless ``--offline`` is given.

Allowed: MIT (and MIT-0), BSD (any clause count), Apache-2.0, PSF / Python-2.0, MPL-2.0,
ISC and the Unlicense (a public-domain dedication).
Denied: GPL, LGPL, AGPL (and anything unknown). An SPDX ``OR`` is satisfied when one
branch is allowed; ``AND`` needs every part allowed.

Usage: python scripts/check_licenses.py [--lock uv.lock] [--offline] [--markdown]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

ROOT_PACKAGE = "woow-nextcloud-mcp-server"

ALLOWED_IDS = {
    "MIT",
    "MIT-0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD",
    "Apache-2.0",
    "PSF-2.0",
    "Python-2.0",
    "MPL-2.0",
    "ISC",
    "Unlicense",
}

CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: MIT No Attribution License (MIT-0)": "MIT-0",
    "License :: OSI Approved :: BSD License": "BSD",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
}

FREE_TEXT = {
    "mit": "MIT",
    "mit license": "MIT",
    "bsd": "BSD",
    "bsd license": "BSD",
    "new bsd": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "3-clause bsd": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "psf": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "mpl 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
}

DENIED = re.compile(r"(^|[^A-Z])(A|L)?GPL|GNU (GENERAL|LESSER|LIBRARY|AFFERO)", re.IGNORECASE)


@dataclass
class Result:
    name: str
    version: str
    licence: str
    source: str
    ok: bool


# ------------------------------------------------------------------------- closure


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure(lock_path: Path) -> list[tuple[str, str]]:
    """(name, version) of every runtime package in the lock file, all platforms."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages: dict[str, list[dict]] = {}
    for package in lock["package"]:
        packages.setdefault(_norm(package["name"]), []).append(package)

    def pick(name: str, version: str | None) -> list[dict]:
        candidates = packages.get(_norm(name), [])
        if version is not None:
            candidates = [p for p in candidates if p["version"] == version] or candidates
        return candidates

    root = pick(ROOT_PACKAGE, None)
    if not root:
        raise SystemExit(f"{ROOT_PACKAGE} not found in {lock_path}")
    seen: set[tuple[str, str, str]] = set()
    found: set[tuple[str, str]] = set()
    queue: list[tuple[dict, tuple[str, ...]]] = [(root[0], ())]
    while queue:
        package, extras = queue.pop()
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        for dep in deps:
            for target in pick(dep["name"], dep.get("version")):
                dep_extras = tuple(dep.get("extra", []))
                key = (_norm(target["name"]), target["version"], ",".join(dep_extras))
                if key in seen:
                    continue
                seen.add(key)
                found.add((target["name"], target["version"]))
                queue.append((target, dep_extras))
    return sorted(found, key=lambda item: (_norm(item[0]), item[1]))


# ------------------------------------------------------------------------- licences


def _tokens(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expression)


def spdx_allowed(expression: str) -> bool:
    """Evaluate an SPDX expression: OR needs one allowed branch, AND needs all."""
    tokens = _tokens(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def factor() -> bool:
        item = take()
        if item == "(":
            value = expr()
            if peek() == ")":
                take()
            return value
        if peek() is not None and peek().upper() == "WITH":  # type: ignore[union-attr]
            take()
            take()
        return item in ALLOWED_IDS and not DENIED.search(item)

    def term() -> bool:
        value = factor()
        while peek() is not None and peek().upper() == "AND":  # type: ignore[union-attr]
            take()
            value = factor() and value
        return value

    def expr() -> bool:
        value = term()
        while peek() is not None and peek().upper() == "OR":  # type: ignore[union-attr]
            take()
            value = term() or value
        return value

    try:
        return bool(tokens) and expr() and position == len(tokens)
    except IndexError:
        return False


def classify(
    expression: str | None, classifiers: list[str], free_text: str | None
) -> tuple[str, bool]:
    """Return (licence description, allowed?) from the three metadata sources."""
    if expression:
        return expression, spdx_allowed(expression)
    licence_classifiers = [c for c in classifiers if c.startswith("License ::")]
    mapped = [CLASSIFIERS.get(c) for c in licence_classifiers]
    if licence_classifiers:
        if any(DENIED.search(c) for c in licence_classifiers) and not any(mapped):
            return " OR ".join(licence_classifiers), False
        names = [m for m in mapped if m]
        if names:
            # several licence classifiers mean a choice (dual licensing)
            return " OR ".join(names), True
    if free_text:
        text = free_text.strip()
        first_line = text.splitlines()[0].strip().rstrip(".") if text else ""
        lowered = first_line.lower()
        name = FREE_TEXT.get(lowered)
        if name is None:
            for key, value in FREE_TEXT.items():
                if lowered.startswith(key + " license"):
                    name = value
                    break
        if name:
            return name, True
        if DENIED.search(text):
            return first_line[:60], False
        if spdx_allowed(first_line):
            return first_line, True
        return f"unknown ({first_line[:40]!r})", False
    return "unknown", False


def installed_metadata(name: str, version: str) -> tuple[str | None, list[str], str | None] | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None
    if dist.version != version:
        return None
    meta = dist.metadata
    return (
        meta.get("License-Expression"),
        meta.get_all("Classifier") or [],
        meta.get("License"),
    )


def pypi_metadata(name: str, version: str) -> tuple[str | None, list[str], str | None]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as answer:
        info = json.load(answer)["info"]
    return info.get("license_expression"), info.get("classifiers") or [], info.get("license")


def check(lock_path: Path, *, offline: bool) -> list[Result]:
    results: list[Result] = []
    for name, version in runtime_closure(lock_path):
        meta = installed_metadata(name, version)
        source = "installed"
        if meta is None:
            if offline:
                results.append(Result(name, version, "not installed here", "-", False))
                continue
            meta = pypi_metadata(name, version)
            source = "pypi"
        licence, ok = classify(*meta)
        results.append(Result(name, version, licence, source, ok))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_lock = Path(__file__).resolve().parents[1] / "uv.lock"
    parser.add_argument("--lock", type=Path, default=default_lock)
    parser.add_argument("--offline", action="store_true", help="never query PyPI")
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table")
    args = parser.parse_args(argv)

    results = check(args.lock, offline=args.offline)
    if args.markdown:
        print("| Package | Version | Licence |")
        print("|---|---|---|")
        for r in results:
            print(f"| {r.name} | {r.version} | {r.licence} |")
    else:
        width = max(len(r.name) for r in results)
        for r in results:
            status = "ok  " if r.ok else "FAIL"
            print(f"{status} {r.name:<{width}} {r.version:<12} {r.licence}  [{r.source}]")
    bad = [r for r in results if not r.ok]
    print(
        f"\n{len(results)} runtime packages checked, {len(bad)} not allowed.",
        file=sys.stderr,
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
