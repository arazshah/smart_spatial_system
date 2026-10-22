"""csv_xy_loader: encodings, delimiters, Persian digits, rejected rows, limits."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")

import plugins.csv_xy_loader as module  # noqa: E402
from plugins.csv_xy_loader import (  # noqa: E402
    decode_bytes,
    load_csv_points,
    parse_number,
    sniff_delimiter,
)


@pytest.fixture(autouse=True)
def _open_roots(monkeypatch):
    monkeypatch.setattr(module, "load_config", lambda _id: {"allowed_roots": []})


def write(tmp_path, text, name="t.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


# ----------------------------------------------------------------- primitives
@pytest.mark.parametrize("text, expected", [
    ("35.6892", 35.6892), ("۳۵٫۶۸۹۲", 35.6892), ("٣٥.٦٨", 35.68), ("35,6892", 35.6892),
    ("1,234,567", 1234567.0), ("-51.4", -51.4), ("1e3", 1000.0), (" 7 ", 7.0),
    ("abc", None), ("", None), (None, None), ("nan", None), ("inf", None), ("1.2.3", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_decode_detects_utf8_bom_and_windows_1256():
    persian = "نام,طول\nتهران,51.4\n"
    arabic_letters = "نام,طول\nتهران,51.4\n".replace("ی", "ي")
    assert decode_bytes(persian.encode("utf-8"), None) == (persian, "utf-8")
    assert decode_bytes(b"\xef\xbb\xbf" + persian.encode("utf-8"), None)[1] == "utf-8-sig"
    assert persian.encode("windows-1256", errors="ignore")  # sanity: the file is legacy-encoded
    legacy = arabic_letters.encode("windows-1256")
    assert decode_bytes(legacy, None) == (persian, "windows-1256")  # Arabic yeh mapped to Persian
    assert decode_bytes(legacy, "cp1256")[0] == persian
    with pytest.raises(ValueError, match="encoding"):
        decode_bytes(b"abc", "shift-jis")


def test_sniff_delimiter():
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a\tb\n1\t2\n") == "\t"
    assert sniff_delimiter("a|b|c\n1|2|3\n") == "|"
    assert sniff_delimiter("a,b\n1,2\n") == ","


# ---------------------------------------------------------------------- loader
def test_basic_load_with_autodetected_columns_and_types(tmp_path):
    path = write(tmp_path, "name,lon,lat,pop,code\nA,51.4,35.7,100,007\nB,52.0,36.0,200,008\n")
    out = load_csv_points(path)
    assert len(out.features) == 2
    props = out.features[0]["properties"]
    assert props == {"name": "A", "pop": 100, "code": "007"}  # zero-padded ids stay text
    assert out.features[0]["geometry"] == {"type": "Point", "coordinates": [51.4, 35.7]}
    assert out.metadata["x_field"] == "lon" and out.metadata["crs"] == "EPSG:4326"
    assert out.metadata["numeric_columns"] == ["pop"] and "path" not in str(out.metadata)


def test_windows_1256_semicolon_persian_digits(tmp_path):
    # windows-1256 has no Persian yeh / digits, so Iranian Excel exports use the Arabic yeh and
    # ASCII digits; the Persian digits path is covered by test_parse_number.
    text = "نام;طول جغرافيايي;عرض جغرافيايي;جمعيت\nتهران;51.389;35.689;9000000\n"
    out = load_csv_points(write(tmp_path, text, encoding="windows-1256"))
    feature = out.features[0]
    assert feature["geometry"]["coordinates"] == [51.389, 35.689]
    assert feature["properties"] == {"نام": "تهران", "جمعیت": 9000000}
    assert out.metadata["encoding"] == "windows-1256" and out.metadata["delimiter"] == ";"


def test_rejected_rows_are_counted_and_sampled(tmp_path):
    text = "id,lon,lat\n1,51,35\n2,,35\n3,abc,35\n4,200,35\n5,51,95\n\n6,52,36\n"
    out = load_csv_points(write(tmp_path, text))
    assert [f["properties"]["id"] for f in out.features] == [1, 6]
    assert out.metadata["rejected_count"] == 4
    reasons = {r["row"]: r["reason"] for r in out.metadata["rejected_sample"]}
    assert "swapped" in reasons[5] and "missing" in reasons[3]
    with pytest.raises(ValueError, match="Row 3"):
        load_csv_points(write(tmp_path, text, "u.csv"), on_invalid="raise")


def test_projected_crs_skips_the_wgs84_range_check(tmp_path):
    out = load_csv_points(write(tmp_path, "x,y\n500000,3950000\n"), crs=32639)
    assert out.features[0]["geometry"]["coordinates"] == [500000.0, 3950000.0]
    assert out.metadata["crs"] == "EPSG:32639"
    with pytest.raises(ValueError, match="crs"):
        load_csv_points(write(tmp_path, "x,y\n1,2\n", "c.csv"), crs="WGS 84")


def test_explicit_fields_wkt_and_errors(tmp_path):
    out = load_csv_points(write(tmp_path, "a,b,v\n1,2,x\n"), x_field="a", y_field="b")
    assert out.features[0]["geometry"]["coordinates"] == [1.0, 2.0]
    wkt = load_csv_points(write(tmp_path, 'g,v\n"POINT (51 35)",1\n"LINESTRING (0 0, 1 1)",2\nbad,3\n', "w.csv"),
                          wkt_field="g")
    assert [f["geometry"]["type"] for f in wkt.features] == ["Point", "LineString"]
    assert wkt.metadata["rejected_count"] == 1
    with pytest.raises(ValueError, match="detect"):
        load_csv_points(write(tmp_path, "foo,bar\n1,2\n", "n.csv"))
    with pytest.raises(ValueError, match="not found"):
        load_csv_points(write(tmp_path, "a,b\n1,2\n", "m.csv"), x_field="zzz", y_field="b")
    with pytest.raises(ValueError, match="header"):
        load_csv_points(write(tmp_path, "", "e.csv"))


def test_limits(tmp_path, monkeypatch):
    path = write(tmp_path, "lon,lat\n" + "".join(f"{i},{i}\n" for i in range(10)))
    out = load_csv_points(path, max_rows=3)
    assert len(out.features) == 3 and out.metadata["truncated"] is True
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_rows": 5})
    with pytest.raises(ValueError, match="more than 5 rows"):
        load_csv_points(path)
    monkeypatch.setattr(module, "load_config", lambda _id: {"max_bytes": 10})
    with pytest.raises(ValueError, match="limit is 10"):
        load_csv_points(path)


def test_path_validation_and_allowed_roots(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    inside = tmp_path / "data"
    inside.mkdir()
    (inside / "ok.csv").write_text("lon,lat\n1,2\n")
    (other / "no.csv").write_text("lon,lat\n1,2\n")
    (inside / "x.exe").write_text("lon,lat\n1,2\n")
    monkeypatch.setattr(module, "load_config", lambda _id: {"allowed_roots": [str(inside)]})
    assert len(load_csv_points(str(inside / "ok.csv")).features) == 1
    with pytest.raises(ValueError, match="allowed root"):
        load_csv_points(str(other / "no.csv"))
    with pytest.raises(ValueError):
        load_csv_points(str(inside / "x.exe"))
    with pytest.raises(FileNotFoundError):
        load_csv_points(str(inside / "missing.csv"))
