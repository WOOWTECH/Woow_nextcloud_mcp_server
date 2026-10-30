"""errors.mask_credentials: the one masking routine for backend error detail."""

from __future__ import annotations

import base64
import json
from urllib.parse import quote

import pytest

from fake_nextcloud import LOGIN
from nextcloud_mcp_server.errors import REDACTED, credential_forms, mask_credentials

PASSWORD = 'FAKE-v016-pass/+"quoted-4917ÿ~'
PAIR = f"{LOGIN}:{PASSWORD}"
CREDENTIALS = (PASSWORD, PAIR)


def forms(value: str) -> dict[str, str]:
    """Every form a credential is masked in, built independently of the implementation."""
    found = {
        "raw": value,
        "url": quote(value, safe=""),
        "json": json.dumps(value)[1:-1],
        "json-unicode": json.dumps(value, ensure_ascii=False)[1:-1],
    }
    for name, encode in (("base64", base64.b64encode), ("base64url", base64.urlsafe_b64encode)):
        encoded = encode(value.encode()).decode()
        found[name] = encoded
        found[f"{name}-unpadded"] = encoded.rstrip("=")
    # Inside a JSON string "/" may be written "\/" (RFC 8259); PHP's json_encode() does so
    # by default.
    for name in ("json", "json-unicode", "base64", "base64-unpadded"):
        found[f"{name}-solidus"] = found[name].replace("/", "\\/")
    return found


@pytest.mark.parametrize("credential", CREDENTIALS, ids=["password", "basic-pair"])
@pytest.mark.parametrize("form", list(forms(PASSWORD)))
def test_every_known_form_is_masked(credential: str, form: str) -> None:
    text = f"storage refused; {forms(credential)[form]}; retry"
    assert mask_credentials(text, CREDENTIALS) == "storage refused; [REDACTED]; retry"


def test_forms_are_distinct_and_known() -> None:
    for credential in CREDENTIALS:
        expected = forms(credential)
        assert len(set(expected.values())) == len(expected)
        assert set(expected.values()) <= credential_forms(credential)
    assert credential_forms("") == set()


def test_text_is_otherwise_unchanged() -> None:
    text = "  SAFE \n diagnostic\t" * 100
    assert mask_credentials(text, CREDENTIALS) == text
    assert mask_credentials(text, ()) == text
    assert mask_credentials(text, ("",)) == text
    # Masking neither folds whitespace nor cuts: the caller does that afterwards.
    assert mask_credentials(f"a  {PASSWORD}\n" * 50, CREDENTIALS) == "a  [REDACTED]\n" * 50


def test_credentials_may_be_any_iterable() -> None:
    assert mask_credentials(f"x {PASSWORD} y", iter([PASSWORD])) == "x [REDACTED] y"
    assert mask_credentials(f"x {PASSWORD} y", [PASSWORD, ""]) == "x [REDACTED] y"


def test_single_pass_longest_first() -> None:
    # The longer credential wins where both match; the shorter one still masks its own.
    assert mask_credentials("long-token token", ("token", "long-token")) == (
        "[REDACTED] [REDACTED]"
    )
    # A credential that occurs in the marker never re-masks a marker emitted before.
    assert mask_credentials("REDACTED and RED", ("REDACTED", "RED")) == (
        "[REDACTED] and [REDACTED]"
    )
    assert mask_credentials("tokentoken", ("token",)) == "[REDACTED][REDACTED]"
    assert REDACTED == "[REDACTED]"


def test_credentials_are_literal_not_patterns() -> None:
    assert mask_credentials("abc .* def", (".*",)) == "abc [REDACTED] def"
    assert mask_credentials("a\\1b \\g<0>", ("\\1", "\\g<0>")) == "a[REDACTED]b [REDACTED]"


def test_pair_masks_whole_basic_value_not_only_the_password() -> None:
    basic = base64.b64encode(PAIR.encode()).decode()
    text = f"Authorization: Basic {basic}; user {LOGIN}"
    assert mask_credentials(text, CREDENTIALS) == f"Authorization: Basic [REDACTED]; user {LOGIN}"


def test_overlapping_occurrences_leave_no_fragment() -> None:
    # A periodic password echoed with one more period.
    assert mask_credentials("xabcabcabcx", ("abcabc",)) == "x[REDACTED]x"
    # Base64 padding that is also the first character of the password.
    password = "=FAKE-pass"
    encoded = base64.b64encode(password.encode()).decode()
    assert encoded.endswith("=")
    text = f"echo {encoded}{password[1:]} end"
    assert mask_credentials(text, (password,)) == "echo [REDACTED] end"
    # Overlapping occurrences become one marker; touching ones stay two.
    assert mask_credentials("aaaa", ("aa",)) == "[REDACTED]"
    assert mask_credentials("aaaa", ("a",)) == "[REDACTED]" * 4


def test_solidus_form_overlapping_another_form_leaves_no_fragment() -> None:
    # The \/ form of the base64 ends with the "=" that starts the JSON form of the password.
    password = "=ÿ_b"
    encoded = base64.b64encode(password.encode()).decode().replace("/", "\\/")
    escaped = json.dumps(password)[1:-1]
    assert encoded.endswith("=") and escaped.startswith("=") and "\\/" in encoded
    assert mask_credentials(f"x {encoded}{escaped[1:]} y", (password,)) == "x [REDACTED] y"
