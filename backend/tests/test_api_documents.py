"""Uploads, listing, details, deletion to Trash, middleware behaviour."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from sidelamp.config import Settings
from tests.conftest import csv_bytes, epub_bytes, make_client, pdf_bytes


def upload(c: TestClient, name: str, data: bytes, **form: str):  # type: ignore[no-untyped-def]
    return c.post("/api/documents", files={"file": (name, data)}, data=form)


def test_upload_pdf_is_saved_and_queued(client: TestClient, settings: Settings) -> None:
    response = upload(client, "report.pdf", pdf_bytes())
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["name"] == "report.pdf"
    assert doc["status"] == "queued"
    assert doc["type"] == "pdf"
    assert (settings.library / "report.pdf").exists()
    listed = client.get("/api/documents").json()["documents"]
    assert [d["id"] for d in listed] == [doc["id"]]
    assert client.get(f"/api/documents/{doc['id']}").json()["sha256"] == doc["sha256"]


def test_name_collision_gets_a_number(client: TestClient, settings: Settings) -> None:
    upload(client, "report.pdf", pdf_bytes(["one"]))
    second = upload(client, "report.pdf", pdf_bytes(["two"])).json()
    third = upload(client, "report.pdf", pdf_bytes(["three"])).json()
    assert second["name"] == "report (2).pdf"
    assert third["name"] == "report (3).pdf"


def test_epub_and_csv_accepted(client: TestClient) -> None:
    assert upload(client, "book.epub", epub_bytes()).status_code == 201
    assert upload(client, "sales.csv", csv_bytes()).status_code == 201


def test_wrong_content_is_refused(client: TestClient, settings: Settings) -> None:
    response = upload(client, "fake.pdf", b"just some text, not a pdf")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "content_mismatch"
    assert not (settings.library / "fake.pdf").exists()
    assert upload(client, "fake.epub", pdf_bytes()).status_code == 415
    assert upload(client, "fake.csv", pdf_bytes()).status_code == 415


def test_unsupported_type_is_refused(client: TestClient) -> None:
    response = upload(client, "notes.docx", b"PK\x03\x04whatever")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_type"


def test_pdf_zip_polyglot_is_refused(client: TestClient) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("payload.txt", "hidden")
    polyglot = pdf_bytes() + buf.getvalue()
    response = upload(client, "poly.pdf", polyglot)
    assert response.status_code == 415


def test_epub_with_zip_slip_entry_is_refused(client: TestClient) -> None:
    data = epub_bytes(extra_entries={"../../escape.txt": b"x"})
    assert upload(client, "evil.epub", data).status_code == 415


def test_csv_with_binary_content_is_refused(client: TestClient) -> None:
    assert upload(client, "data.csv", b"a,b\n1,\x00\x01\x02\n").status_code == 415


def test_empty_file_is_refused(client: TestClient) -> None:
    response = upload(client, "empty.csv", b"")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


def test_oversize_upload_is_refused(settings: Settings) -> None:
    settings.max_upload_mb = 1
    with make_client(settings) as c:
        big = b"a,b\n" + b"1,2\n" * 400_000  # ~1.6 MB
        response = upload(c, "big.csv", big)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "too_large"
        assert not (settings.library / "big.csv").exists()
        assert list(settings.library_paths.tmp_dir.iterdir()) == []


def test_traversal_in_file_name_stays_inside_library(
    client: TestClient, settings: Settings
) -> None:
    response = upload(client, "../../outside.pdf", pdf_bytes())
    assert response.status_code == 201
    assert response.json()["path"] == "outside.pdf"
    assert (settings.library / "outside.pdf").exists()


def test_traversal_in_folder_is_refused(client: TestClient) -> None:
    response = upload(client, "a.pdf", pdf_bytes(), folder="../elsewhere")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_path"


def test_upload_into_subfolder(client: TestClient, settings: Settings) -> None:
    (settings.library / "work").mkdir()
    response = upload(client, "a.pdf", pdf_bytes(), folder="work")
    assert response.json()["path"] == "work/a.pdf"
    assert response.json()["folder"] == "work"


def test_delete_moves_file_to_trash(client: TestClient, settings: Settings) -> None:
    doc = upload(client, "report.pdf", pdf_bytes()).json()
    response = client.delete(f"/api/documents/{doc['id']}")
    assert response.status_code == 200
    assert "Trash" in response.json()["message"]
    assert not (settings.library / "report.pdf").exists()
    trashed = list((settings.data_dir / "trash").rglob("report.pdf"))
    assert len(trashed) == 1
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert client.get("/api/documents").json()["documents"] == []


def test_download_original_file(client: TestClient) -> None:
    doc = upload(client, "sales.csv", csv_bytes()).json()
    response = client.get(f"/api/documents/{doc['id']}/file")
    assert response.status_code == 200
    assert response.content == csv_bytes()
    assert "attachment" in response.headers["content-disposition"]


def test_index_survives_restart(settings: Settings) -> None:
    with make_client(settings) as c:
        doc = upload(c, "report.pdf", pdf_bytes()).json()
    with make_client(settings) as c:
        assert c.get(f"/api/documents/{doc['id']}").status_code == 200


# ---------------------------------------------------------------- middleware


def test_request_id_and_security_headers(client: TestClient) -> None:
    response = client.get("/api/documents")
    headers = response.headers
    assert len(headers["x-request-id"]) >= 8
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    echoed = client.get("/api/health", headers={"X-Request-Id": "abcdef123456"})
    assert echoed.headers["x-request-id"] == "abcdef123456"
    bogus = client.get("/api/health", headers={"X-Request-Id": "<script>"})
    assert bogus.headers["x-request-id"] != "<script>"


def test_errors_are_json_without_internals(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    text = response.text
    assert "Traceback" not in text and str(settings.data_dir) not in text


def test_unexpected_error_hides_details(settings: Settings) -> None:
    from fastapi import APIRouter

    from sidelamp.app import create_app

    app = create_app(settings, background=False)
    router = APIRouter()

    @router.get("/api/boom")
    def boom() -> None:
        raise RuntimeError(f"secret path {settings.data_dir} and key sk-123")

    app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1:8642", client=("127.0.0.1", 1)) as c:
        response = c.get("/api/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sk-123" not in response.text and "secret path" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_validation_error_is_plain(client: TestClient) -> None:
    response = client.post("/api/documents", data={"nofile": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_public_config(client: TestClient, settings: Settings) -> None:
    config = client.get("/api/config").json()
    assert config["app"] == "Sidelamp"
    assert config["library"] == str(settings.library)
    assert config["cloud"] is False
    assert config["needs_setup"] is True


def test_library_folder_layout(client: TestClient, settings: Settings) -> None:
    app_dir = settings.library / ".sidelamp"
    assert (app_dir / "format.json").exists()
    assert (app_dir / "lock").exists()
    for name in ("docs", "chats", "logs", "tmp"):
        assert (app_dir / name).is_dir()
    assert (Path(settings.data_dir) / "trash").is_dir()
