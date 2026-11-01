from __future__ import annotations

import json
from xml.sax.saxutils import escape

import pytest
from fastmcp.exceptions import ToolError

from nextcloud_mcp_server.webdav import (
    DAV,
    file_entry,
    http_date_to_iso,
    parse_multistatus,
    parse_xml,
    safe_server_diagnostic,
    tag,
)

MULTISTATUS = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:oc="http://owncloud.org/ns">
  <D:response>
    <D:href>/remote.php/dav/files/u/Docs/</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype><D:collection/></D:resourcetype>
        <D:getlastmodified>Tue, 06 Oct 2026 10:20:30 GMT</D:getlastmodified>
        <D:getetag>"5f0c"</D:getetag>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
    <D:propstat>
      <D:prop><D:getcontentlength/><D:getcontenttype/></D:prop>
      <D:status>HTTP/1.1 404 Not Found</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/remote.php/dav/files/u/Docs/a%20%231.md</D:href>
    <D:propstat>
      <D:prop>
        <D:resourcetype/>
        <D:getcontentlength>42</D:getcontentlength>
        <D:getlastmodified>Tue, 06 Oct 2026 12:20:30 +0200</D:getlastmodified>
        <D:getetag>W/"abc"</D:getetag>
        <D:getcontenttype>text/markdown</D:getcontenttype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/remote.php/dav/files/u/gone.txt</D:href>
    <D:status>HTTP/1.1 404 Not Found</D:status>
  </D:response>
  <D:response><D:href>  </D:href></D:response>
  <D:response>
    <D:href>/remote.php/dav/files/u/odd</D:href>
    <D:propstat><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
    <D:propstat><D:prop><D:getcontentlength>xx</D:getcontentlength></D:prop></D:propstat>
  </D:response>
</D:multistatus>
"""


def test_parse_multistatus_namespaced_with_404_propstat() -> None:
    items = parse_multistatus(MULTISTATUS)
    assert [i.href for i in items] == [
        "/remote.php/dav/files/u/Docs/",
        "/remote.php/dav/files/u/Docs/a%20%231.md",
        "/remote.php/dav/files/u/odd",
    ]  # gone.txt has a response-level 404 and is left out
    folder, doc, odd = items
    assert folder.status == 200
    assert tag(DAV, "getcontentlength") not in folder.props  # only the 404 propstat had it
    assert folder.has_child(tag(DAV, "resourcetype"), tag(DAV, "collection"))
    assert doc.text(tag(DAV, "getcontentlength")) == "42"
    assert doc.text(tag(DAV, "displayname")) is None

    entry = file_entry(doc, "Docs/a #1.md")
    assert entry == {
        "path": "Docs/a #1.md",
        "name": "a #1.md",
        "type": "file",
        "size": 42,
        "modified": "2026-10-06T10:20:30Z",
        "etag": "abc",
        "content_type": "text/markdown",
    }
    folder_entry = file_entry(folder, "Docs")
    assert folder_entry["type"] == "folder"
    assert folder_entry["size"] is None
    assert folder_entry["etag"] == "5f0c"
    assert folder_entry["content_type"] is None

    odd_entry = file_entry(odd, "odd")
    assert odd_entry["size"] is None  # a non-numeric length is ignored
    assert file_entry(odd, "")["name"] == ""


def test_parse_multistatus_rejects_other_documents() -> None:
    with pytest.raises(ToolError, match="not a WebDAV multistatus"):
        parse_multistatus(b'<x xmlns="DAV:"/>')
    with pytest.raises(ToolError, match="not valid WebDAV XML"):
        parse_multistatus(b"<html>oops")
    with pytest.raises(ToolError, match="not valid WebDAV XML"):
        parse_multistatus(b"")


def test_xml_entities_and_dtds_are_refused() -> None:
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<d:multistatus xmlns:d="DAV:">&lol2;</d:multistatus>"""
    with pytest.raises(ToolError):
        parse_xml(bomb)
    external = b"""<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><x>&xxe;</x>"""
    with pytest.raises(ToolError):
        parse_xml(external)


@pytest.mark.parametrize("shape", ["sabre", "ocs_json", "text"])
def test_safe_server_diagnostic_recognizes_quota_without_reflecting_text(shape: str) -> None:
    message = (
        "FAKE quota exhausted on isolated backend; token=FAKE-unknown-secret; "
        "host=nextcloud-db.internal.svc.cluster.local"
    )
    if shape == "sabre":
        body = (
            '<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
            f"<s:message>{escape(message)}</s:message></d:error>"
        ).encode()
        content_type = "application/xml"
    elif shape == "ocs_json":
        body = json.dumps({"ocs": {"meta": {"message": message}}}).encode()
        content_type = "application/json"
    else:
        body, content_type = message.encode(), "text/plain; charset=utf-8"
    assert safe_server_diagnostic(body, content_type) == "storage quota exhausted"


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"", "text/plain"),
        (b"quota exhausted" * 7000, "text/plain"),
        (b'<d:error xmlns:d="DAV:"/>', "application/xml"),
        (b"<html>quota exhausted</html>", "text/html"),
        (b'{"other": "quota exhausted"}', "application/json"),
        (b'{"ocs": {"meta": {"message": 123}}}', "application/json"),
        (
            b"<!DOCTYPE x [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><x>&xxe;</x>",
            "application/xml",
        ),
        (b"\xffquota exhausted", "text/plain"),
    ],
)
def test_safe_server_diagnostic_rejects_unrecognized_body(
    body: bytes,
    content_type: str,
) -> None:
    assert safe_server_diagnostic(body, content_type) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tue, 06 Oct 2026 10:20:30 GMT", "2026-10-06T10:20:30Z"),
        ("Tue, 06 Oct 2026 12:20:30 +0200", "2026-10-06T10:20:30Z"),
        ("Tue, 06 Oct 2026 10:20:30 -0000", "2026-10-06T10:20:30Z"),
        ("garbage", None),
        ("", None),
        (None, None),
    ],
)
def test_http_date_to_iso(value: str | None, expected: str | None) -> None:
    assert http_date_to_iso(value) == expected
