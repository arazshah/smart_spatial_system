from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.planning.report_spec import (
    MapLayerSpec,
    ReportSpec,
    SummarySpec,
    TableColumnSpec,
    TableSpec,
    default_real_estate_report_spec,
    report_spec_from_dict,
)


def test_default_real_estate_report_spec_structure():
    spec = default_real_estate_report_spec(
        ranked_source="ranked_properties",
        map_sources={
            "buildable_zone": "buildable_zone",
            "poi": "poi",
            "roads": "roads",
        },
    )

    assert spec.title == "گزارش رتبه‌بندی و تحلیل سرمایه‌گذاری ملک"
    assert spec.format == "pdf"
    assert spec.language == "fa"

    # Map layers
    assert len(spec.map_layers) == 4
    sources = [layer.source for layer in spec.map_layers]
    assert "buildable_zone" in sources
    assert "poi" in sources
    assert "roads" in sources
    assert "ranked_properties" in sources

    # Ranked layer must be choropleth
    choropleth = next(l for l in spec.map_layers if l.kind == "choropleth")
    assert choropleth.source == "ranked_properties"
    assert choropleth.style["color_field"] == "investment_score"

    # Table
    assert len(spec.tables) == 1
    table = spec.tables[0]
    assert table.source == "ranked_properties"
    assert table.sort_by == "rank"
    assert table.sort_order == "asc"

    col_fields = [c.field for c in table.columns]
    assert "rank" in col_fields
    assert "investment_score" in col_fields
    assert "distance_to_poi" in col_fields
    assert "flood_risk" in col_fields
    assert "earthquake_risk" in col_fields
    assert "fire_risk" in col_fields

    # Summary
    assert spec.summary is not None
    assert spec.summary.source == "ranked_properties"
    assert "top_score" in spec.summary.stats
    assert "avg_score" in spec.summary.stats


def test_report_spec_from_dict_round_trip():
    original = default_real_estate_report_spec(
        ranked_source="ranked",
        map_sources={"poi": "poi"},
    )

    data = {
        "title": original.title,
        "language": original.language,
        "format": original.format,
        "config": original.config,
        "map_layers": [
            {
                "source": layer.source,
                "kind": layer.kind,
                "label": layer.label,
                "visible": layer.visible,
                "style": layer.style,
            }
            for layer in original.map_layers
        ],
        "tables": [
            {
                "source": t.source,
                "columns": [
                    {
                        "field": c.field,
                        "label": c.label,
                        "format": c.format,
                        "align": c.align,
                        "width": c.width,
                    }
                    for c in t.columns
                ],
                "sort_by": t.sort_by,
                "sort_order": t.sort_order,
                "max_rows": t.max_rows,
                "title": t.title,
            }
            for t in original.tables
        ],
        "summary": {
            "source": original.summary.source,
            "stats": original.summary.stats,
            "template": original.summary.template,
            "language": original.summary.language,
        } if original.summary else None,
    }

    restored = report_spec_from_dict(data)

    assert restored.title == original.title
    assert restored.language == original.language
    assert restored.format == original.format
    assert len(restored.map_layers) == len(original.map_layers)
    assert len(restored.tables) == len(original.tables)
    assert restored.tables[0].source == "ranked"
    assert len(restored.tables[0].columns) == len(original.tables[0].columns)
    assert restored.summary is not None
    assert restored.summary.source == original.summary.source
    assert "top_score" in restored.summary.stats


def test_map_layer_spec_defaults():
    layer = MapLayerSpec(source="my_layer")
    assert layer.kind == "features"
    assert layer.visible is True
    assert layer.style == {}


def test_table_spec_with_no_columns():
    table = TableSpec(source="ranked", max_rows=10)
    assert table.sort_order == "asc"
    assert table.columns == []
    assert table.max_rows == 10


def test_report_spec_custom_title_and_format():
    spec = default_real_estate_report_spec(
        ranked_source="ranked",
        title="گزارش سفارشی",
        format="html",
        language="fa",
    )

    assert spec.title == "گزارش سفارشی"
    assert spec.format == "html"
