from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _resolve_render_pdf_capability():
    """
    Resolve the PDF renderer through the capability boundary.

    Compatibility:
    some focused unit tests inject a lightweight ``plugins.pdf_renderer`` module
    into sys.modules with only a ``render_pdf`` callable and no PLUGIN metadata.
    Honor that preloaded callable without using a static plugin import. In normal
    runtime, resolve via CapabilityRegistry.
    """
    loaded_pdf_module = sys.modules.get("plugins.pdf_renderer")
    loaded_render_pdf = getattr(loaded_pdf_module, "render_pdf", None)
    if callable(loaded_render_pdf):
        return loaded_render_pdf

    from orchestrator.capability_registry import CapabilityRegistry
    from orchestrator.plugin_modules import DEFAULT_SAFE_PLUGIN_MODULES

    return CapabilityRegistry.from_plugin_modules(
        DEFAULT_SAFE_PLUGIN_MODULES
    ).resolve("render_pdf")


def try_render_real_estate_ranking_document(
    *,
    report: dict[str, Any],
    table_rows: list[dict[str, Any]],
    ranked_geojson: dict[str, Any],
    summary: dict[str, Any],
    request_id: str,
    build_pdf_report_payload: Callable[..., dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    warnings: list[str] = []
    trace_step: dict[str, Any] = {
        "order": 5,
        "node_id": "node_005_render_pdf",
        "capability_name": "render_pdf",
        "plugin_id": "pdf_renderer",
        "output_kind": "document",
        "status": "skipped",
    }

    try:
        render_pdf = _resolve_render_pdf_capability()
    except Exception as exc:
        warnings.append(f"PDF renderer import failed: {exc}")
        trace_step.update(
            {
                "status": "failed",
                "error": str(exc),
            }
        )
        return documents, warnings, trace_step

    pdf_report = build_pdf_report_payload(
        report=report,
        table_rows=table_rows,
        ranked_geojson=ranked_geojson,
        summary=summary,
    )

    safe_request_id = str(request_id or "request").replace("/", "_")
    output_dir = Path("artifacts") / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"real_estate_ranking_{safe_request_id}.pdf"

    try:
        pdf_out = render_pdf(
            pdf_report,
            output_path=str(output_path),
            save_to_disk=True,
            metadata={
                "request_id": request_id,
                "domain": "real_estate_spatial_ranking",
                "report_id": "real_estate_ranking_report",
            },
        )
    except Exception as exc:
        warnings.append(f"PDF render failed unexpectedly: {exc}")
        trace_step.update(
            {
                "status": "failed",
                "error": str(exc),
            }
        )
        return documents, warnings, trace_step

    pdf_dict = pdf_out.to_dict() if hasattr(pdf_out, "to_dict") else {}

    if getattr(pdf_out, "success", False) and getattr(pdf_out, "file_path", None):
        pdf_file_path = str(pdf_out.file_path)
        pdf_filename = Path(pdf_file_path).name
        pdf_download_url = f"/requests/{request_id}/documents/{pdf_filename}"

        documents.append(
            {
                "id": "real_estate_ranking_pdf",
                "name": "real_estate_ranking_report.pdf",
                "filename": pdf_filename,
                "format": "pdf",
                "role": "downloadable_report",
                "mime_type": "application/pdf",
                "path": pdf_file_path,
                "file_path": pdf_file_path,
                "download_url": pdf_download_url,
                "preview_url": pdf_download_url,
                "size_bytes": len(getattr(pdf_out, "pdf_bytes", b"") or b""),
                "meta": getattr(pdf_out, "meta", {}) or pdf_dict.get("meta", {}),
            }
        )
        trace_step.update(
            {
                "status": "success",
                "artifact_id": "real_estate_ranking_pdf",
                "path": pdf_out.file_path,
            }
        )
        return documents, warnings, trace_step

    html = getattr(pdf_out, "html", "") or ""
    errors = getattr(pdf_out, "errors", []) or pdf_dict.get("errors", [])

    if html:
        documents.append(
            {
                "id": "real_estate_ranking_html",
                "name": "real_estate_ranking_report.html",
                "format": "html",
                "role": "printable_report_fallback",
                "mime_type": "text/html",
                "content": html,
                "size_bytes": len(html.encode("utf-8")),
                "meta": getattr(pdf_out, "meta", {}) or pdf_dict.get("meta", {}),
                "errors": errors,
            }
        )
        warnings.append(
            "PDF rendering was not completed; HTML fallback document was returned."
        )
        trace_step.update(
            {
                "status": "warning",
                "artifact_id": "real_estate_ranking_html",
                "errors": errors,
            }
        )
        return documents, warnings, trace_step

    warnings.append("PDF rendering failed and no HTML fallback was produced.")
    trace_step.update(
        {
            "status": "failed",
            "errors": errors,
        }
    )
    return documents, warnings, trace_step
