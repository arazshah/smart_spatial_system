from plugins.postgis_connector import _escape_literal_percent_for_pyformat


def test_escape_literal_percent_keeps_placeholders_and_escapes_persian_like_patterns():
    sql = """
    SELECT *
    FROM public.planet_osm_point
    WHERE name ILIKE '%مترو%'
       OR name ILIKE '%metro%'
       OR ST_SRID(way) = %s
    LIMIT %s
    """

    escaped = _escape_literal_percent_for_pyformat(sql)

    assert "ILIKE '%%مترو%%'" in escaped
    assert "ILIKE '%%metro%%'" in escaped
    assert "ST_SRID(way) = %s" in escaped
    assert "LIMIT %s" in escaped


def test_escape_literal_percent_is_idempotent_for_already_escaped_patterns():
    sql = "SELECT * FROM t WHERE name ILIKE '%%metro%%' LIMIT %s"

    escaped = _escape_literal_percent_for_pyformat(sql)

    assert "ILIKE '%%metro%%'" in escaped
    assert "LIMIT %s" in escaped
