"""HTTP-level integration tests for the POST-5.1 B2 attachment endpoints,
via FastAPI's `TestClient` -- mirrors `test_api_app.py`'s own dependency-
override pattern. No real GCS/Cloud SQL anywhere in this file (a fake
storage double + in-memory SQLite attachment repository + in-memory ADK
sessions, exactly like `test_attachment_upload_service.py`).
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.app import app
from backend.api.session_service import ApiSessionService, get_session_service
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService, get_attachment_service
from backend.attachments.storage import get_attachment_storage
from backend.config.settings import Settings, get_settings
from backend.tests.test_attachment_upload_service import _FakeAttachmentStorage


def _png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(1, 2, 3)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def attachment_service():
    repository = AttachmentRepository("sqlite+aiosqlite:///:memory:")
    return AttachmentService(repository)


@pytest.fixture()
def storage() -> _FakeAttachmentStorage:
    return _FakeAttachmentStorage()


@pytest.fixture()
def client(session_service: ApiSessionService, attachment_service: AttachmentService, storage: _FakeAttachmentStorage):
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_attachment_storage] = lambda: storage
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]


# --- upload ----------------------------------------------------------------


def test_upload_png_success(client: TestClient) -> None:
    session_id = _create_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["mime_type"] == "image/png"
    assert body["status"] == "ready"
    assert body["filename"] == "screenshot.png"
    assert isinstance(body["attachment_id"], str) and body["attachment_id"]
    assert body["size_bytes"] > 0


def test_upload_jpeg_success(client: TestClient) -> None:
    session_id = _create_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    assert response.json()["mime_type"] == "image/jpeg"


def test_upload_no_file_returns_a_safe_400(client: TestClient) -> None:
    """FastAPI's own request-shape validation (a missing required `file`
    part) is already intercepted application-wide by
    `handle_request_validation_error` (backend/api/errors.py) and mapped
    to the same `validation_error` (400) SafeError shape every other
    malformed request in this API produces -- confirmed against the
    existing `test_malformed_request_missing_message_field_returns_a_safe_400`
    convention in test_api_app.py. FastAPI's raw 422 never surfaces here.
    """
    session_id = _create_session(client)
    response = client.post(f"/api/sessions/{session_id}/attachments")
    assert response.status_code == 400
    assert response.json()["errorCode"] == "validation_error"


def test_upload_mismatched_declared_mime_returns_415(client: TestClient) -> None:
    session_id = _create_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("fake.png", _jpeg_bytes(), "image/png")},
    )
    assert response.status_code == 415


def test_upload_corrupt_bytes_returns_415(client: TestClient) -> None:
    session_id = _create_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("bad.png", b"not an image", "image/png")},
    )
    assert response.status_code == 415


def test_upload_oversized_returns_413(session_service, attachment_service, storage) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_attachment_storage] = lambda: storage
    data = _png_bytes((64, 64))
    app.dependency_overrides[get_settings] = lambda: Settings(
        env={"SLOPANOC_CHAT_ATTACHMENT_MAX_BYTES": str(len(data) - 1)}
    )
    try:
        with TestClient(app) as c:
            session_response = c.post("/api/sessions")
            session_id = session_response.json()["session_id"]
            response = c.post(
                f"/api/sessions/{session_id}/attachments",
                files={"file": ("big.png", data, "image/png")},
            )
        assert response.status_code == 413
    finally:
        app.dependency_overrides.clear()


def test_upload_unknown_session_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/sessions/does-not-exist/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 404


def test_upload_error_response_has_no_internal_detail(client: TestClient) -> None:
    response = client.post(
        f"/api/sessions/does-not-exist/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    body = response.json()
    assert set(body) == {"errorCode", "userMessage", "retryable", "correlationId"}


# --- metadata retrieval ------------------------------------------------


def test_get_metadata_success(client: TestClient) -> None:
    session_id = _create_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    attachment_id = upload.json()["attachment_id"]

    response = client.get(f"/api/attachments/{attachment_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["attachment_id"] == attachment_id
    assert body["status"] == "ready"


def test_get_metadata_response_never_leaks_internal_fields(client: TestClient) -> None:
    session_id = _create_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    attachment_id = upload.json()["attachment_id"]

    response = client.get(f"/api/attachments/{attachment_id}")
    body = response.json()
    assert set(body) == {"attachment_id", "filename", "mime_type", "size_bytes", "status"}
    body_text = response.text
    assert "gs://" not in body_text
    assert "storage_object_name" not in body_text
    assert "owner_user_id" not in body_text
    assert "sha256" not in body_text
    assert "bucket" not in body_text.lower()


def test_get_metadata_missing_attachment_returns_404(client: TestClient) -> None:
    response = client.get("/api/attachments/does-not-exist")
    assert response.status_code == 404


# --- content retrieval ---------------------------------------------------


def test_get_content_success_byte_identical(client: TestClient) -> None:
    session_id = _create_session(client)
    data = _png_bytes()
    upload = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", data, "image/png")},
    )
    attachment_id = upload.json()["attachment_id"]

    response = client.get(f"/api/attachments/{attachment_id}/content")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private"
    assert "screenshot.png" in response.headers["content-disposition"]


def test_get_content_response_never_contains_gs_uri(client: TestClient) -> None:
    session_id = _create_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/attachments",
        files={"file": ("screenshot.png", _png_bytes(), "image/png")},
    )
    attachment_id = upload.json()["attachment_id"]

    response = client.get(f"/api/attachments/{attachment_id}/content")
    assert b"gs://" not in response.content
    for value in response.headers.values():
        assert "gs://" not in value


def test_get_content_missing_attachment_returns_404(client: TestClient) -> None:
    response = client.get("/api/attachments/does-not-exist/content")
    assert response.status_code == 404


# --- cross-user authorization -------------------------------------------


def test_cross_user_metadata_access_denied(session_service, attachment_service, storage) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_attachment_storage] = lambda: storage
    try:
        with TestClient(app) as c:
            session_response = c.post("/api/sessions", headers={"X-SLOPANOC-DEV-USER": "alice"})
            session_id = session_response.json()["session_id"]
            upload = c.post(
                f"/api/sessions/{session_id}/attachments",
                files={"file": ("screenshot.png", _png_bytes(), "image/png")},
                headers={"X-SLOPANOC-DEV-USER": "alice"},
            )
            attachment_id = upload.json()["attachment_id"]

            response = c.get(f"/api/attachments/{attachment_id}", headers={"X-SLOPANOC-DEV-USER": "mallory"})
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cross_user_content_access_denied(session_service, attachment_service, storage) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_attachment_storage] = lambda: storage
    try:
        with TestClient(app) as c:
            session_response = c.post("/api/sessions", headers={"X-SLOPANOC-DEV-USER": "alice"})
            session_id = session_response.json()["session_id"]
            upload = c.post(
                f"/api/sessions/{session_id}/attachments",
                files={"file": ("screenshot.png", _png_bytes(), "image/png")},
                headers={"X-SLOPANOC-DEV-USER": "alice"},
            )
            attachment_id = upload.json()["attachment_id"]

            response = c.get(
                f"/api/attachments/{attachment_id}/content", headers={"X-SLOPANOC-DEV-USER": "mallory"}
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cross_user_session_upload_denied(session_service, attachment_service, storage) -> None:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_attachment_service] = lambda: attachment_service
    app.dependency_overrides[get_attachment_storage] = lambda: storage
    try:
        with TestClient(app) as c:
            session_response = c.post("/api/sessions", headers={"X-SLOPANOC-DEV-USER": "alice"})
            session_id = session_response.json()["session_id"]

            response = c.post(
                f"/api/sessions/{session_id}/attachments",
                files={"file": ("screenshot.png", _png_bytes(), "image/png")},
                headers={"X-SLOPANOC-DEV-USER": "mallory"},
            )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
