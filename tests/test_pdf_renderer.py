from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from orchestrator.planning.op_catalog import get_op, is_supported
from orchestrator.planning.report_spec import default_real_estate_report_spec
from plugins.pdf_renderer import PDFOut, render_pdf
from plugins.report_builder import ReportOut, build_report


def _make_report() -> ReportOut:
    features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.4, 35.7]},
                "properties": {
                    "id": "p1",
                    "name": "ویلای لوکس",
                    "rank": 1,
                    "investment_score": 87.5,
                    "distance_to_poi": 120.0,
                    "distance_to_road": 80.0,
                    "inside_buildable_zone": True,
                    "flood_risk": "low",
                    "earthquake_risk": "medium",
                    "fire_risk": "low",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [51.5, 35.6]},
                "properties": {
                    "id": "p2",
                    "name": "آپارتمان مرکز",
                    "rank": 2,
                    "investment_score": 71.2,
                    "distance_to_poi": 340.0,
                    "distance_to_road": 210.0,
                    "inside_buildable_zone": True,
                    "flood_risk": "low",
                    "earthquake_risk": "low",
                    "fire_risk": "low",
                },
            },
        ],
    }

    spec = default_real_estate_report_spec(ranked_source="ranked")
    return build_report(features, report_spec=spec)


def test_render_pdf_returns_pdf_out():
    report = _make_report()
    result = render_pdf(report, save_to_disk=False)

    assert isinstance(result, PDFOut)


def test_render_pdf_produces_html():
    report = _make_report()
    result = render_pdf(report, save_to_disk=False)

    assert len(result.html) > 100
    assert "ویلای لوکس" in result.html
    assert "گزارش رتبه‌بندی" in result.html


def test_render_pdf_html_contains_table_data():
    report = _make_report()
    result = render_pdf(report, save_to_disk=False)

    assert "87.5" in result.html or "87" in result.html
    assert "71.2" in result.html or "71" in result.html
    assert "آپارتمان مرکز" in result.html


def test_render_pdf_saves_to_disk(tmp_path):
    report = _make_report()
    output = str(tmp_path / "test_report.pdf")

    result = render_pdf(report, output_path=output, save_to_disk=True)

    if result.success:
        assert result.file_path == output
        assert Path(output).exists()
        assert Path(output).stat().st_size > 0
    else:
        # WeasyPrint may not be available in test environment.
        assert "PDF render failed" in (result.errors[0] if result.errors else "")
        assert len(result.html) > 100


def test_render_pdf_from_dict_report():
    report = _make_report()
    report_dict = report.to_dict()

    result = render_pdf(report_dict, save_to_disk=False)

    assert isinstance(result, PDFOut)
    assert "ویلای لوکس" in result.html


def test_render_pdf_with_missing_template():
    report = _make_report()
    result = render_pdf(
        report,
        template_name="nonexistent_template.html",
        save_to_disk=False,
    )

    assert result.success is False
    assert any("Template not found" in e for e in result.errors)


def test_op_catalog_contains_render_pdf():
    assert is_supported("render_pdf")

    op = get_op("render_pdf")
    assert op.capability_name == "render_pdf"
    assert op.input_map["report"] == "report"
    assert op.output_type == "pdf"


def test_render_pdf_meta_fields():
    report = _make_report()
    result = render_pdf(report, save_to_disk=False)

    assert "template" in result.meta
    assert "generated_at" in result.meta
    assert "plugin" in result.meta
    assert result.meta["plugin"] == "pdf_renderer"
