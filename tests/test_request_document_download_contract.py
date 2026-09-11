"""
Regression tests for the request document download contract.

The API document endpoint must remain domain-agnostic.  It should not hard-code
real_estate filenames; it should serve any generated request document under the
reports artifact directory when the filename is safe and request-bound.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.responses import FileResponse

from api.routers.requests_outputs import download_request_document

REQUEST_OUTPUTS_PATH = Path("api/routers/requests_outputs.py")


def test_request_document_download_endpoint_is_domain_agnostic() -> None:
    source = REQUEST_OUTPUTS_PATH.read_text(encoding="utf-8")

    assert "real_estate_ranking_{request_id}.pdf" not in source
    assert "expected_filename" not in source
    assert "artifacts" in source
    assert "reports" in source


def test_download_request_document_serves_generic_request_bound_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    request_id = "req-generic-doc-001"
    reports_dir = tmp_path / "artifacts" / "reports"
    reports_dir.mkdir(parents=True)

    filename = f"analysis_report_{request_id}.html"
    document_path = reports_dir / filename
    document_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

    response = download_request_document(
        request=None,  # type: ignore[arg-type]
        request_id=request_id,
        filename=filename,
    )

    assert isinstance(response, FileResponse)
    assert Path(response.path).resolve() == document_path.resolve()
    assert response.media_type == "text/html"
    assert response.filename == filename


def test_download_request_document_rejects_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        download_request_document(
            request=None,  # type: ignore[arg-type]
            request_id="req-1",
            filename="../req-1.pdf",
        )

    assert exc_info.value.status_code == 404


def test_download_request_document_rejects_document_for_other_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    reports_dir = tmp_path / "artifacts" / "reports"
    reports_dir.mkdir(parents=True)

    filename = "analysis_report_req-other.pdf"
    (reports_dir / filename).write_bytes(b"%PDF-1.4")

    with pytest.raises(HTTPException) as exc_info:
        download_request_document(
            request=None,  # type: ignore[arg-type]
            request_id="req-current",
            filename=filename,
        )

    assert exc_info.value.status_code == 404
