"""
pdf_renderer.py

PDF Renderer Plugin
===================

Plugin ID:
    pdf_renderer

Capability:
    - render_pdf

Purpose:
    Convert ReportOut (structured report JSON) to PDF file using
    Jinja2 HTML template + WeasyPrint.

Output:
    PDFOut — contains PDF bytes, file path, and metadata.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geochat_sdk.decorators import capability
from geochat_sdk.plugin import auto_collect

from plugins.report_builder import ReportOut

PLUGIN_ID = "pdf_renderer"

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"
DEFAULT_TEMPLATE = "real_estate_report.html"

RISK_LABELS_FA = {
    "very_low": "خیلی کم",
    "low": "کم",
    "medium": "متوسط",
    "high": "زیاد",
    "very_high": "خیلی زیاد",
    "critical": "بحرانی",
}

LAYER_COLORS = [
    "#22c55e",
    "#3b82f6",
    "#f59e0b",
    "#e94560",
    "#8b5cf6",
    "#06b6d4",
]


# ------------------------------------------------------------------ #
# PDFOut
# ------------------------------------------------------------------ #

@dataclass
class PDFOut:
    """
    PDF render output.

    Attributes:
        pdf_bytes:      Raw PDF file bytes.
        file_path:      Path to saved PDF file (if save_to_disk=True).
        html:           Rendered HTML string (for debugging).
        meta:           Render metadata.
        success:        Whether rendering succeeded.
        errors:         List of errors/warnings.
    """
    pdf_bytes: bytes
    file_path: str | None
    html: str
    meta: dict[str, Any]
    success: bool = True
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "pdf_size_bytes": len(self.pdf_bytes),
            "html_size_bytes": len(self.html),
            "meta": self.meta,
            "success": self.success,
            "errors": self.errors,
        }


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _format_datetime_fa(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str))
        return dt.strftime("%Y-%m-%d  %H:%M UTC")
    except Exception:
        return str(iso_str)


def _resolve_report_out(report: Any) -> ReportOut:
    if isinstance(report, ReportOut):
        return report

    if isinstance(report, dict):
        return ReportOut(
            meta=report.get("meta", {}),
            summary=report.get("summary", {}),
            table=report.get("table", {"title": "", "columns": [], "rows": [], "total_rows": 0}),
            map_layers=report.get("map_layers", []),
            spec=report.get("spec", {}),
            success=report.get("success", True),
            errors=report.get("errors", []),
        )

    raise ValueError(
        f"report must be ReportOut or dict. Got: {type(report).__name__}"
    )


def _render_html(
    report: ReportOut,
    template_path: Path,
) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:
        raise ImportError(
            "Jinja2 is required for PDF rendering. "
            "Install it with: pip install jinja2"
        ) from exc

    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
    )

    template = env.get_template(template_path.name)

    context: dict[str, Any] = {
        "meta": report.meta,
        "summary": report.summary,
        "table": report.table,
        "map_layers": report.map_layers,
        "spec": report.spec,
        "generated_at_fa": _format_datetime_fa(
            report.meta.get("generated_at")
        ),
        "risk_labels": RISK_LABELS_FA,
        "layer_colors": LAYER_COLORS,
    }

    return template.render(**context)


def _render_pdf_bytes(html: str) -> bytes:
    try:
        import weasyprint
    except ImportError as exc:
        raise ImportError(
            "WeasyPrint is required for PDF rendering. "
            "Install it with: pip install weasyprint"
        ) from exc

    buf = io.BytesIO()
    weasyprint.HTML(string=html).write_pdf(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ #
# Capability: render_pdf
# ------------------------------------------------------------------ #

@capability(
    name="render_pdf",
    keywords=[
        "render pdf",
        "generate pdf",
        "export pdf",
        "pdf report",
        "pdf output",
        "گزارش PDF",
        "خروجی PDF",
        "تولید PDF",
        "صدور گزارش",
    ],
    description=(
        "Render a structured ReportOut to a PDF file using "
        "Jinja2 HTML template and WeasyPrint."
    ),
    required_inputs=["report"],
    optional_inputs=[
        "template_name",
        "output_path",
        "save_to_disk",
        "metadata",
    ],
    output_kind="pdf",
    permissions=[],
    metadata={
        "category": "report",
        "data_type": "pdf",
        "operation": "render_pdf",
        "returns": "PDFOut",
        "artifact_kind": "pdf",
        "access_scope": "reporting",
        "config_aware": False,
        "routable": True,
        "module_name": "plugins.pdf_renderer",
    },
)
def render_pdf(
    report: ReportOut | dict[str, Any],
    template_name: str = DEFAULT_TEMPLATE,
    output_path: str | None = None,
    save_to_disk: bool = True,
    metadata: dict[str, Any] | None = None,
) -> PDFOut:
    """
    Render ReportOut to PDF.

    Args:
        report:
            ReportOut or dict-serialized ReportOut.

        template_name:
            Jinja2 HTML template filename inside templates/reports/.
            Default: real_estate_report.html

        output_path:
            File path to save PDF.
            If None and save_to_disk=True, saves to a temp file.

        save_to_disk:
            Whether to save PDF to disk.

        metadata:
            Extra metadata to include in output.
    """
    errors: list[str] = []

    report_out = _resolve_report_out(report)

    template_path = TEMPLATES_DIR / template_name

    if not template_path.exists():
        return PDFOut(
            pdf_bytes=b"",
            file_path=None,
            html="",
            meta={"error": f"Template not found: {template_path}"},
            success=False,
            errors=[f"Template not found: {template_path}"],
        )

    # 1) Render HTML
    try:
        html = _render_html(report_out, template_path)
    except Exception as exc:
        return PDFOut(
            pdf_bytes=b"",
            file_path=None,
            html="",
            meta={"error": str(exc)},
            success=False,
            errors=[f"HTML render failed: {exc}"],
        )

    # 2) Render PDF bytes
    try:
        pdf_bytes = _render_pdf_bytes(html)
    except Exception as exc:
        errors.append(f"PDF render failed: {exc}")
        # Return HTML-only result so caller can still use HTML.
        return PDFOut(
            pdf_bytes=b"",
            file_path=None,
            html=html,
            meta={
                "template": template_name,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "html_only": True,
                **(metadata or {}),
            },
            success=False,
            errors=errors,
        )

    # 3) Optionally save to disk
    saved_path: str | None = None

    if save_to_disk:
        if output_path:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(pdf_bytes)
            saved_path = str(dest)
        else:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                delete=False,
                prefix="spatial_report_",
            ) as tmp:
                tmp.write(pdf_bytes)
                saved_path = tmp.name

    render_meta: dict[str, Any] = {
        "template": template_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_size_bytes": len(pdf_bytes),
        "html_size_bytes": len(html),
        "file_path": saved_path,
        "plugin": PLUGIN_ID,
        **(metadata or {}),
    }

    return PDFOut(
        pdf_bytes=pdf_bytes,
        file_path=saved_path,
        html=html,
        meta=render_meta,
        success=True,
        errors=errors,
    )


PLUGIN = auto_collect(
    id=PLUGIN_ID,
    version="1.0.0",
    name="PDF Renderer",
    description="Render structured ReportOut to PDF using Jinja2 + WeasyPrint.",
    author="GeoChat Platform Team",
    permissions=[],
)
