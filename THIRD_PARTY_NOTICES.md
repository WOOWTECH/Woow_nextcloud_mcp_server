# Third-party notices

Woow Nextcloud MCP Server (MIT, Copyright (c) 2026 WOOWTECH) does not vendor any
third-party source code. At run time it depends on the following packages, installed
from PyPI. Each is distributed under its own licence, reproduced in the package's
distribution files; the licences below are taken from the package metadata.

The table is the complete runtime dependency closure of `uv.lock` for all platforms
(development tools such as pytest and ruff are not included). It is produced and
checked by `python scripts/check_licenses.py --markdown`; CI fails on GPL, LGPL, AGPL
or unknown licences. Some packages are only installed on some platforms (for example
`pywin32`, `pywin32-ctypes` and `tzdata` on Windows, `jeepney` and `secretstorage`
on Linux).

Direct dependencies: `fastmcp` 3.4.5, `mcp` 1.28.1, `httpx` 0.28.1,
`pydantic-settings` 2.15.0, `defusedxml` 0.7.1, `typing-extensions` 4.16.0 and, on
Windows, `tzdata` 2026.5.

| Package | Version | Licence |
|---|---|---|
| aiofile | 3.12.3 | Apache-2.0 |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| authlib | 1.8.0 | BSD |
| backports-tarfile | 1.2.0 | MIT |
| beartype | 0.22.9 | MIT |
| cachetools | 7.2.1 | MIT |
| caio | 0.12.9 | Apache-2.0 |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| cyclopts | 5.2.0 | Apache-2.0 |
| defusedxml | 0.7.1 | PSF-2.0 |
| dnspython | 2.8.0 | ISC |
| docstring-parser | 0.18.0 | MIT |
| email-validator | 2.3.0 | Unlicense |
| exceptiongroup | 1.3.1 | MIT |
| fastmcp | 3.4.5 | Apache-2.0 |
| fastmcp-slim | 3.4.5 | Apache-2.0 |
| griffelib | 2.3.2 | ISC |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD |
| httpx-sse | 0.4.3 | MIT |
| idna | 3.20 | BSD-3-Clause |
| importlib-metadata | 9.0.1 | Apache-2.0 |
| jaraco-classes | 3.4.0 | MIT |
| jaraco-context | 6.1.2 | MIT |
| jaraco-functools | 4.6.0 | MIT |
| jeepney | 0.9.0 | MIT |
| joserfc | 1.7.5 | BSD |
| jsonref | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-path | 0.5.0 | Apache-2.0 |
| jsonschema-specifications | 2025.9.1 | MIT |
| keyring | 25.7.0 | MIT |
| markdown-it-py | 4.2.0 | MIT |
| mcp | 1.28.1 | MIT |
| mdurl | 0.1.2 | MIT |
| more-itertools | 11.1.0 | MIT |
| openapi-pydantic | 0.6.0 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pathable | 0.6.0 | Apache-2.0 |
| platformdirs | 4.12.3 | MIT |
| py-key-value-aio | 0.4.6 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pyjwt | 2.15.1 | MIT |
| pyperclip | 1.11.0 | BSD |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pywin32 | 312 | PSF-2.0 |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| pyyaml | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| rich | 15.0.0 | MIT |
| rich-rst | 2.2.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| secretstorage | 3.5.0 | BSD-3-Clause |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| uncalled-for | 0.4.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.2 | BSD-3-Clause |
| zipp | 4.1.1 | MIT |

Licence notes:

- `cryptography`, `packaging`: dual licensed (Apache-2.0 OR BSD); used under either.
- `certifi`: MPL-2.0 (file-level copyleft; used unmodified as a dependency).
- `email-validator`: The Unlicense (public-domain dedication), pulled in by
  `pydantic[email]` through FastMCP.
- `cffi`: MIT-0.
