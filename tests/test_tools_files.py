from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp.exceptions import ToolError

from fake_nextcloud import BASE_URL, FakeNextcloud
from nextcloud_mcp_server.tools import DELETE_NOTE, NextcloudTools

HOME = f"{BASE_URL}/remote.php/dav/files/alice%20smith/"


def _dav_requests(fake: FakeNextcloud) -> list[httpx.Request]:
    return [r for r in fake.requests if "/remote.php/dav/" in str(r.url)]


# ------------------------------------------------------------------------------- tree


async def test_tree_home_depth1(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    result = await tools.get_file_tree("/")
    assert result["path"] == ""
    assert result["truncated"] is False
    assert [(e["path"], e["type"]) for e in result["entries"]] == [
        ("Docs", "folder"),
        ("Empty", "folder"),
        ("photo.png", "file"),
    ]
    photo = result["entries"][2]
    assert photo == {
        "path": "photo.png",
        "name": "photo.png",
        "type": "file",
        "size": 10,
        "modified": "2026-10-06T10:20:30Z",
        "etag": fake.files["photo.png"].etag,
        "content_type": "image/png",
    }
    (request,) = _dav_requests(fake)
    assert request.method == "PROPFIND"
    assert str(request.url) == HOME
    assert request.headers["Depth"] == "1"
    assert request.headers["Content-Type"] == "application/xml; charset=utf-8"
    body = fake.bodies[fake.requests.index(request)]
    for prop in (
        b"<d:resourcetype/>",
        b"<d:getcontentlength/>",
        b"<d:getlastmodified/>",
        b"<d:getetag/>",
        b"<d:getcontenttype/>",
    ):
        assert prop in body


async def test_tree_depth3_preorder(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    result = await tools.get_file_tree("", depth=3)
    assert [e["path"] for e in result["entries"]] == [
        "Docs",
        "Docs/Sub",
        "Docs/Sub/deep.txt",
        "Docs/a #1.md",
        "Docs/readme.md",
        "Empty",
        "photo.png",
    ]
    urls = [str(r.url) for r in _dav_requests(fake)]
    assert urls == [HOME, HOME + "Docs", HOME + "Empty", HOME + "Docs/Sub"]


async def test_tree_subfolder_with_special_names(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.add_file("我的 #資料?/100% a+b.txt", b"x")
    result = await tools.get_file_tree("/我的 #資料?/")
    assert result["path"] == "我的 #資料?"
    assert [e["path"] for e in result["entries"]] == ["我的 #資料?/100% a+b.txt"]
    request = _dav_requests(fake)[-1]
    assert request.url.raw_path.decode().endswith(
        "/alice%20smith/%E6%88%91%E7%9A%84%20%23%E8%B3%87%E6%96%99%3F"
    )


async def test_tree_on_a_file(tools: NextcloudTools) -> None:
    result = await tools.get_file_tree("Docs/readme.md", depth=3)
    assert [e["path"] for e in result["entries"]] == ["Docs/readme.md"]
    assert result["truncated"] is False


async def test_tree_truncation(fake: FakeNextcloud, make_settings) -> None:
    settings = make_settings(tree_max_entries=4)
    tools = _tools(fake, settings)
    result = await tools.get_file_tree("", depth=3)
    assert len(result["entries"]) == 4
    assert result["truncated"] is True

    exact = _tools(fake, make_settings(tree_max_entries=3))
    result = await exact.get_file_tree("", depth=1)
    assert len(result["entries"]) == 3
    assert result["truncated"] is False
    result = await exact.get_file_tree("", depth=2)
    assert result["truncated"] is True  # more levels to list, but no room left

    small = _tools(fake, make_settings(tree_max_entries=2))
    result = await small.get_file_tree("")
    assert [e["path"] for e in result["entries"]] == ["Docs", "Empty"]
    assert result["truncated"] is True


async def test_tree_skips_vanished_subfolders(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    def override(request: httpx.Request) -> httpx.Response | None:
        if request.url.raw_path.endswith(b"/Empty"):
            return httpx.Response(404)
        if request.url.raw_path.endswith(b"/Docs"):
            return httpx.Response(403)
        return None

    fake.override = override
    result = await tools.get_file_tree("", depth=2)
    assert [e["path"] for e in result["entries"]] == ["Docs", "Empty", "photo.png"]


async def test_tree_subfolder_server_error_propagates(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.override = lambda r: httpx.Response(500) if r.url.raw_path.endswith(b"/Docs") else None
    with pytest.raises(ToolError, match=r"Nextcloud returned an error \(500\)"):
        await tools.get_file_tree("", depth=2)


async def test_tree_missing_folder(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match=r'^"nope" does not exist\.$'):
        await tools.get_file_tree("nope")


async def test_tree_ignores_foreign_hrefs(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    xml = (
        b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
        b"<d:response><d:href>/elsewhere/x</d:href><d:propstat><d:prop><d:resourcetype/>"
        b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"<d:response><d:href>/nc/remote.php/dav/files/alice%20smith/Docs/deeper/x</d:href>"
        b"<d:propstat><d:prop><d:resourcetype/></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        b"</d:multistatus>"
    )
    fake.override = lambda r: httpx.Response(207, content=xml) if r.method == "PROPFIND" else None
    result = await tools.get_file_tree("Docs")
    assert result["entries"] == []


async def test_tree_too_large_listing(tools: NextcloudTools, monkeypatch) -> None:
    import nextcloud_mcp_server.client as client_module

    monkeypatch.setattr(client_module, "MAX_XML_BYTES", 10)
    with pytest.raises(ToolError, match=r'^The listing of "/" is too large to process\.$'):
        await tools.get_file_tree("")


# ------------------------------------------------------------------------------- read


async def test_read_text_file(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    result = await tools.read_text_file("/Docs/a #1.md")
    assert result == {
        "path": "Docs/a #1.md",
        "etag": fake.files["Docs/a #1.md"].etag,
        "bytes": 9,
        "content_type": "text/markdown",
        "content": "hash name",
    }
    propfind, get = _dav_requests(fake)
    assert propfind.method == "PROPFIND" and propfind.headers["Depth"] == "0"
    assert get.method == "GET"
    assert str(get.url) == HOME + "Docs/a%20%231.md"
    assert await tools.get_file_content("Docs/a #1.md") == "hash name"


async def test_read_strips_bom_but_counts_it(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.add_file("bom.txt", "﻿hello 世界".encode())
    result = await tools.read_text_file("bom.txt")
    assert result["content"] == "hello 世界"
    assert result["bytes"] == 3 + len("hello 世界".encode())


@pytest.mark.parametrize("data", [b"\xff\xfe\x00a", b"abc\x00def", b"\xc3\x28"])
async def test_read_refuses_binary(tools: NextcloudTools, fake: FakeNextcloud, data: bytes) -> None:
    fake.add_file("bin.dat", data)
    with pytest.raises(ToolError) as info:
        await tools.read_text_file("bin.dat")
    assert str(info.value) == (
        '"bin.dat" is not a UTF-8 text file; use get_file_tree to see its type.'
    )


async def test_read_refuses_folder_and_home(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match=r'^"Docs" is a folder; use get_file_tree'):
        await tools.read_text_file("Docs/")
    with pytest.raises(ToolError, match="home folder"):
        await tools.read_text_file("/")


async def test_read_missing(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match=r'^"Docs/none.txt" does not exist\.$'):
        await tools.read_text_file("Docs/none.txt")


async def test_read_size_cap_from_propfind(fake: FakeNextcloud, make_settings) -> None:
    tools = _tools(fake, make_settings(max_text_bytes=5))
    with pytest.raises(ToolError, match=r'"Docs/readme.md" is too large .*8 bytes, limit 5'):
        await tools.read_text_file("Docs/readme.md")
    assert not [r for r in _dav_requests(fake) if r.method == "GET"]  # refused before download


async def test_read_size_cap_while_streaming(fake: FakeNextcloud, make_settings) -> None:
    tools = _tools(fake, make_settings(max_text_bytes=5))
    fake.add_file("grows.txt", b"abc")

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET":
            return httpx.Response(200, content=b"0123456789")
        return None

    fake.override = override
    with pytest.raises(ToolError, match=r'"grows.txt" is too large for the text tools \(limit 5'):
        await tools.read_text_file("grows.txt")


async def test_read_exactly_at_cap(fake: FakeNextcloud, make_settings) -> None:
    tools = _tools(fake, make_settings(max_text_bytes=8))
    assert (await tools.read_text_file("Docs/readme.md"))["content"] == "# Hello\n"


async def test_read_uses_propfind_etag_when_get_has_none(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.override = lambda r: httpx.Response(200, content=b"hi") if r.method == "GET" else None
    result = await tools.read_text_file("Docs/readme.md")
    assert result["etag"] == fake.files["Docs/readme.md"].etag
    assert result["content"] == "hi"


# ------------------------------------------------------------------------------- create


async def test_create_text_file(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    result = await tools.create_text_file("Docs/new #1?.md", "héllo\n")
    etag = fake.files["Docs/new #1?.md"].etag
    assert result == {"status": "created", "path": "Docs/new #1?.md", "etag": etag, "bytes": 7}
    (put,) = [r for r in fake.requests if r.method == "PUT"]
    assert str(put.url) == HOME + "Docs/new%20%231%3F.md"
    assert put.headers["If-None-Match"] == "*"
    assert "If-Match" not in put.headers
    assert put.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert fake.files["Docs/new #1?.md"].data == "héllo\n".encode()


async def test_create_plain_text_content_type(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    await tools.create_text_file("notes.txt", "x")
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["Content-Type"] == "text/plain; charset=utf-8"


async def test_create_never_overwrites(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    before = fake.files["Docs/readme.md"].data
    with pytest.raises(ToolError) as info:
        await tools.create_text_file("Docs/readme.md", "other")
    assert str(info.value) == (
        '"Docs/readme.md" already exists; read it first and use update_text_file with its etag.'
    )
    assert fake.files["Docs/readme.md"].data == before


async def test_create_checks(fake: FakeNextcloud, make_settings) -> None:
    tools = _tools(fake, make_settings(readonly=False, max_text_bytes=4))
    with pytest.raises(ToolError, match="5 bytes as UTF-8, more than the 4-byte limit"):
        await tools.create_text_file("a.txt", "hello")
    with pytest.raises(ToolError, match="lone surrogates"):
        await tools.create_text_file("a.txt", "\ud800")
    with pytest.raises(ToolError, match="must name a file"):
        await tools.create_text_file("/", "x")
    with pytest.raises(ToolError, match=r"\.\."):
        await tools.create_text_file("../x", "x")
    assert fake.requests == []  # all refused before any request


async def test_create_missing_parent_and_folder(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match=r'^The parent folder of "No/x.txt" does not exist\.$'):
        await tools.create_text_file("No/x.txt", "x")
    with pytest.raises(ToolError, match=r'^"Docs" is a folder or cannot be written\.$'):
        await tools.create_text_file("Docs", "x")


async def test_etag_from_propfind_when_put_has_none(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.send_put_etag = False
    result = await tools.create_text_file("e.txt", "x")
    assert result["etag"] == fake.files["e.txt"].etag
    last = fake.requests[-1]
    assert last.method == "PROPFIND" and last.headers["Depth"] == "0"
    assert b"<d:getetag/>" in fake.bodies[-1]


async def test_etag_none_when_nothing_reports_it(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.send_put_etag = False
    fake.override = lambda r: httpx.Response(500) if r.method == "PROPFIND" else None
    result = await tools.create_text_file("e.txt", "x")
    assert result["etag"] is None


async def test_etag_none_when_propfind_has_no_etag(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.send_put_etag = False
    empty = (
        b'<d:multistatus xmlns:d="DAV:"><d:response><d:href>/x</d:href><d:propstat><d:prop>'
        b"<d:getetag/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        b"</d:response></d:multistatus>"
    )
    fake.override = lambda r: httpx.Response(207, content=empty) if r.method == "PROPFIND" else None
    assert (await tools.create_text_file("e.txt", "x"))["etag"] is None


# ------------------------------------------------------------------------------- update


async def test_update_text_file(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    old = fake.files["Docs/readme.md"].etag
    result = await tools.update_text_file("Docs/readme.md", "# New\n", f'"{old}"')
    new = fake.files["Docs/readme.md"].etag
    assert result == {
        "status": "updated",
        "path": "Docs/readme.md",
        "previous_etag": old,
        "etag": new,
        "bytes": 6,
    }
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["If-Match"] == f'"{old}"'
    assert "If-None-Match" not in put.headers
    assert put.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert fake.files["Docs/readme.md"].data == b"# New\n"


async def test_update_stale_etag(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    current = fake.files["Docs/readme.md"].etag
    with pytest.raises(ToolError) as info:
        await tools.update_text_file("Docs/readme.md", "x", 'W/"stale"')
    assert str(info.value) == (
        f'"Docs/readme.md" changed since it was read (current etag {current}); '
        "read it again and retry."
    )
    assert fake.files["Docs/readme.md"].data == b"# Hello\n"


async def test_update_of_missing_file(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    with pytest.raises(ToolError) as info:
        await tools.update_text_file("Docs/missing.md", "x", "abc")
    assert str(info.value) == (
        '"Docs/missing.md" does not exist; use create_text_file to create it.'
    )


async def test_upload_replace_of_missing_file(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError) as info:
        await tools.upload_file("gone.bin", "AAEC", "abc")
    assert str(info.value) == (
        '"gone.bin" does not exist; upload it without expected_etag to create it.'
    )


async def test_delete_412_when_file_vanished(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    etag = fake.files["photo.png"].etag

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "DELETE":
            del fake.files["photo.png"]
            return httpx.Response(412)
        return None

    fake.override = override
    with pytest.raises(ToolError) as info:
        await tools.delete_file_checked("photo.png", etag)
    assert str(info.value) == '"photo.png" does not exist; nothing was deleted.'


async def test_update_stale_etag_unknown_current(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    fake.override = lambda r: httpx.Response(412) if r.method == "PUT" else httpx.Response(500)
    with pytest.raises(ToolError, match=r"current etag unknown"):
        await tools.update_text_file("Docs/readme.md", "x", "abc")


async def test_update_requires_etag(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    with pytest.raises(ToolError, match="expected_etag must not be empty"):
        await tools.update_text_file("Docs/readme.md", "x", '""')
    with pytest.raises(ToolError, match="not a valid etag"):
        await tools.update_text_file("Docs/readme.md", "x", 'a"b')
    assert fake.requests == []


async def test_update_other_error(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.override = lambda r: httpx.Response(423) if r.method == "PUT" else None
    with pytest.raises(ToolError, match=r'^"Docs/readme.md" is locked'):
        await tools.update_text_file("Docs/readme.md", "x", "abc")


# ------------------------------------------------------------------------------- upload


async def test_upload_create_and_replace(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    payload = bytes(range(256))
    encoded = base64.b64encode(payload).decode()
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    created = await tools.upload_file("Docs/img #1.png", wrapped)
    first = fake.files["Docs/img #1.png"].etag
    assert created == {"status": "created", "path": "Docs/img #1.png", "etag": first, "bytes": 256}
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["If-None-Match"] == "*"
    assert put.headers["Content-Type"] == "image/png"
    assert fake.files["Docs/img #1.png"].data == payload

    replaced = await tools.upload_file("Docs/img #1.png", base64.b64encode(b"v2").decode(), first)
    assert replaced["status"] == "replaced"
    assert replaced["bytes"] == 2
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["If-Match"] == f'"{first}"'
    assert "If-None-Match" not in put.headers


async def test_upload_unknown_extension(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    await tools.upload_file("blob.zzz-unknown", "AAEC")
    put = [r for r in fake.requests if r.method == "PUT"][-1]
    assert put.headers["Content-Type"] == "application/octet-stream"


async def test_upload_create_conflict(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match=r'^"photo.png" already exists; get its etag'):
        await tools.upload_file("photo.png", "AAEC")


async def test_upload_replace_stale(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    current = fake.files["photo.png"].etag
    with pytest.raises(ToolError, match=f"current etag {current}"):
        await tools.upload_file("photo.png", "AAEC", "old")


async def test_upload_other_error(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    fake.override = lambda r: httpx.Response(507) if r.method == "PUT" else None
    with pytest.raises(ToolError, match=r'^Not enough storage or file too large for "x.bin"\.$'):
        await tools.upload_file("x.bin", "AAEC")


@pytest.mark.parametrize("bad", ["abc", "a@==", "AAE=C", "____", "äbcd"])
async def test_upload_rejects_bad_base64(
    tools: NextcloudTools, fake: FakeNextcloud, bad: str
) -> None:
    with pytest.raises(ToolError, match="not valid base64"):
        await tools.upload_file("x.bin", bad)
    assert fake.requests == []


async def test_upload_size_cap(fake: FakeNextcloud, make_settings) -> None:
    tools = _tools(fake, make_settings(readonly=False, max_upload_bytes=4))
    with pytest.raises(ToolError, match="decoded file is 5 bytes"):
        await tools.upload_file("x.bin", base64.b64encode(b"12345").decode())
    with pytest.raises(ToolError, match="would be larger than the 4-byte upload limit"):
        await tools.upload_file("x.bin", base64.b64encode(b"x" * 100).decode())
    assert (await tools.upload_file("x.bin", base64.b64encode(b"1234").decode()))["bytes"] == 4
    assert (await tools.upload_file("empty.bin", ""))["bytes"] == 0


async def test_upload_empty_etag_is_refused(tools: NextcloudTools) -> None:
    with pytest.raises(ToolError, match="expected_etag must not be empty"):
        await tools.upload_file("x.bin", "AAEC", "")


# ------------------------------------------------------------------------------- delete


async def test_delete_file_checked(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    etag = fake.files["Docs/a #1.md"].etag
    result = await tools.delete_file_checked("Docs/a #1.md", etag)
    assert result == {
        "status": "deleted",
        "path": "Docs/a #1.md",
        "etag": etag,
        "note": DELETE_NOTE,
    }
    assert "Docs/a #1.md" not in fake.files
    propfind, delete = _dav_requests(fake)
    assert propfind.method == "PROPFIND" and propfind.headers["Depth"] == "0"
    assert delete.method == "DELETE"
    assert str(delete.url) == HOME + "Docs/a%20%231.md"
    assert delete.headers["If-Match"] == f'"{etag}"'


async def test_delete_refuses_folder(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    with pytest.raises(ToolError) as info:
        await tools.delete_file_checked("Docs", "folder-4")
    assert str(info.value) == '"Docs" is a folder; this tool deletes single files only.'
    assert not [r for r in fake.requests if r.method == "DELETE"]


async def test_delete_stale_etag_is_refused_before_delete(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    current = fake.files["photo.png"].etag
    with pytest.raises(ToolError, match=f"current etag {current}"):
        await tools.delete_file_checked("photo.png", "stale")
    assert "photo.png" in fake.files
    assert not [r for r in fake.requests if r.method == "DELETE"]


async def test_delete_412_from_server(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    etag = fake.files["photo.png"].etag

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "DELETE":
            fake.files["photo.png"].etag = "changed"
            return httpx.Response(412)
        return None

    fake.override = override
    with pytest.raises(ToolError, match="current etag changed"):
        await tools.delete_file_checked("photo.png", etag)


async def test_delete_missing_and_other_errors(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    with pytest.raises(ToolError, match=r'^"gone.txt" does not exist\.$'):
        await tools.delete_file_checked("gone.txt", "x")
    fake.override = lambda r: httpx.Response(403) if r.method == "DELETE" else None
    etag = fake.files["photo.png"].etag
    with pytest.raises(ToolError, match=r'^Permission denied for "photo.png"\.$'):
        await tools.delete_file_checked("photo.png", etag)


async def test_delete_without_propfind_etag(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    xml = (
        b'<d:multistatus xmlns:d="DAV:"><d:response>'
        b"<d:href>/nc/remote.php/dav/files/alice%20smith/photo.png</d:href>"
        b"<d:propstat><d:prop><d:resourcetype/></d:prop>"
        b"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    )
    fake.override = lambda r: httpx.Response(207, content=xml) if r.method == "PROPFIND" else None
    etag = fake.files["photo.png"].etag
    result = await tools.delete_file_checked("photo.png", etag)
    assert result["status"] == "deleted"


async def test_stat_without_own_entry(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    xml = b'<d:multistatus xmlns:d="DAV:"></d:multistatus>'
    fake.override = lambda r: httpx.Response(207, content=xml) if r.method == "PROPFIND" else None
    with pytest.raises(ToolError, match=r'^"photo.png" does not exist\.$'):
        await tools.read_text_file("photo.png")


def _tools(fake: FakeNextcloud, settings) -> NextcloudTools:
    from nextcloud_mcp_server.client import NextcloudClient

    return NextcloudTools(NextcloudClient(settings, transport=fake.transport), settings)


# ------------------------------------------------------------------------------- server hrefs

_PREFIX = "/nc/remote.php/dav/files/alice%20smith/"


def _ms(*responses: str) -> bytes:
    return ('<d:multistatus xmlns:d="DAV:">' + "".join(responses) + "</d:multistatus>").encode()


def _resp(href: str, folder: bool = False, status: str | None = None) -> str:
    rtype = "<d:collection/>" if folder else ""
    if status:
        return f"<d:response><d:href>{href}</d:href><d:status>{status}</d:status></d:response>"
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{rtype}"
        '</d:resourcetype><d:getetag>"e"</d:getetag></d:prop>'
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


async def test_unsafe_server_hrefs_are_dropped_and_never_followed(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    listing = _ms(
        _resp(_PREFIX, folder=True),
        _resp(_PREFIX + "..", folder=True),
        _resp(_PREFIX + "ok/", folder=True),
        _resp(_PREFIX + "bad%E2%80%AEname", folder=False),
        _resp(_PREFIX + "x%0Ay.txt"),
        _resp(_PREFIX + "gone.txt", status="HTTP/1.1 404 Not Found"),
    )
    seen: list[bytes] = []

    def override(request: httpx.Request) -> httpx.Response | None:
        seen.append(request.url.raw_path)
        if request.url.raw_path.endswith(b"/ok"):
            return httpx.Response(207, content=_ms(_resp(_PREFIX + "ok/", folder=True)))
        return httpx.Response(207, content=listing)

    fake.override = override
    result = await tools.get_file_tree("", depth=3)
    assert [e["path"] for e in result["entries"]] == ["ok"]
    assert all(b".." not in path for path in seen)
    assert len(seen) == 2  # home + "ok" only


async def test_depth0_answer_with_foreign_href_is_the_resource(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    single = _ms(
        "<d:response><d:href>/other-webroot/remote.php/dav/files/alice/Docs/readme.md</d:href>"
        "<d:propstat><d:prop><d:resourcetype/><d:getcontentlength>8</d:getcontentlength>"
        '<d:getetag>"abc"</d:getetag></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
        "</d:propstat></d:response>"
    )

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "PROPFIND":
            return httpx.Response(207, content=single)
        return None

    fake.override = override
    result = await tools.read_text_file("Docs/readme.md")
    assert result["content"] == "# Hello\n"
    tree = await tools.get_file_tree("Docs/readme.md")
    assert tree["entries"][0]["path"] == "Docs/readme.md"


async def test_listing_with_only_foreign_hrefs_explains_webroot(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    foreign = _ms(
        _resp("/other/remote.php/dav/files/alice/", folder=True),
        _resp("/other/remote.php/dav/files/alice/a.txt"),
    )
    fake.override = lambda r: httpx.Response(207, content=foreign)
    with pytest.raises(ToolError, match="overwritewebroot") as info:
        await tools.get_file_tree("")
    assert "NEXTCLOUD_MCP_BASE_URL" in str(info.value)


async def test_large_listing_is_parsed_in_a_thread(
    tools: NextcloudTools, fake: FakeNextcloud, monkeypatch
) -> None:
    import nextcloud_mcp_server.tools as tools_module

    calls: list[str] = []
    original = tools_module.asyncio.to_thread

    async def to_thread(fn, *args):
        calls.append(fn.__name__)
        return await original(fn, *args)

    monkeypatch.setattr(tools_module, "OFFLOAD_BYTES", 10)
    monkeypatch.setattr(tools_module.asyncio, "to_thread", to_thread)
    result = await tools.get_file_tree("")
    assert len(result["entries"]) == 3
    assert calls == ["parse_multistatus"]


async def test_write_answer_too_large_is_a_tool_error(
    tools: NextcloudTools, fake: FakeNextcloud, monkeypatch
) -> None:
    import nextcloud_mcp_server.client as client_module

    monkeypatch.setattr(client_module, "MAX_XML_BYTES", 3)
    fake.override = lambda r: (
        httpx.Response(201, content=b"0123456789") if r.method == "PUT" else None
    )
    with pytest.raises(ToolError, match=r'answer for "big.txt" is larger than 3 bytes'):
        await tools.create_text_file("big.txt", "x")


async def test_unchanged_etag_after_write_adds_note(
    tools: NextcloudTools, fake: FakeNextcloud
) -> None:
    from nextcloud_mcp_server.tools import UNCHANGED_ETAG_NOTE

    old = fake.files["Docs/readme.md"].etag
    keep = {"etag": old}

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method == "PUT":
            return httpx.Response(204, headers={"ETag": f'"{keep["etag"]}"'})
        return None

    fake.override = override
    updated = await tools.update_text_file("Docs/readme.md", "x", old)
    assert updated["etag"] == old
    assert updated["note"] == UNCHANGED_ETAG_NOTE

    photo = fake.files["photo.png"].etag
    keep["etag"] = photo
    replaced = await tools.upload_file("photo.png", "AAEC", photo)
    assert replaced["status"] == "replaced"
    assert replaced["note"] == UNCHANGED_ETAG_NOTE


async def test_changed_etag_has_no_note(tools: NextcloudTools, fake: FakeNextcloud) -> None:
    old = fake.files["Docs/readme.md"].etag
    assert "note" not in await tools.update_text_file("Docs/readme.md", "x", old)
    photo = fake.files["photo.png"].etag
    assert "note" not in await tools.upload_file("photo.png", "AAEC", photo)
    assert "note" not in await tools.upload_file("new.bin", "AAEC")
