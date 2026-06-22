"""
Generic PostGIS semantic resolver.

Purpose
-------
After a PostGIS connection is established, the system should understand the
database semantically instead of letting the LLM guess table names, column names,
or raw SQL predicates.

This module provides:
- PostGIS schema discovery
- semantic concept inference from user text
- schema-aware predicate building
- semantic concept -> PostGIS layer candidate resolution

Important:
This is not a patch for a specific query. It is a generic foundation for
mapping concepts such as parks, hospitals, schools, metro stations, shopping
centers, roads, buildings, etc. to real PostGIS tables and valid predicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str = ""
    udt_name: str = ""


@dataclass(frozen=True)
class PostGISTableInfo:
    schema: str
    table: str
    geom_col: str
    geometry_type: str = ""
    srid: int | None = None
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)
    estimated_rows: int | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}

    def _lookup(self) -> dict[str, ColumnInfo]:
        return {c.name.lower(): c for c in self.columns}

    def column_info(self, name: str) -> ColumnInfo | None:
        return self._lookup().get(str(name).lower())

    def real_column_name(self, name: str) -> str | None:
        c = self.column_info(name)
        return c.name if c else None

    def has_column(self, name: str) -> bool:
        return self.column_info(name) is not None


@dataclass(frozen=True)
class PostGISSchemaContext:
    tables: tuple[PostGISTableInfo, ...] = field(default_factory=tuple)

    def spatial_tables(self) -> tuple[PostGISTableInfo, ...]:
        return self.tables

    def find_table(
        self,
        *,
        schema: str | None = None,
        table: str | None = None,
        geom_col: str | None = None,
    ) -> PostGISTableInfo | None:
        for t in self.tables:
            if schema is not None and t.schema != schema:
                continue
            if table is not None and t.table != table:
                continue
            if geom_col is not None and t.geom_col != geom_col:
                continue
            return t
        return None


@dataclass(frozen=True)
class PredicateBuildResult:
    where: str | None
    used_terms: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    skipped_terms: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return bool(self.where)


@dataclass(frozen=True)
class SemanticLayerCandidate:
    concept: str
    schema: str
    table: str
    geom_col: str
    geometry_type: str
    srid: int | None
    where: str
    columns: tuple[str, ...]
    score: float
    used_terms: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    skipped_terms: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_query_database_params(
        self,
        *,
        source_type: str = "postgis",
        mode: str = "select_table",
        geom_alias: str = "geom",
        limit: int = 5000,
        output_srid: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "source_type": source_type,
            "mode": mode,
            "schema": self.schema,
            "table": self.table,
            "columns": list(self.columns),
            "geom_col": self.geom_col,
            "geom_alias": geom_alias,
            "where": self.where,
            "limit": limit,
        }
        if output_srid is not None:
            params["output_srid"] = output_srid
        return params


def _normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ")
    return " ".join(value.split())


def _quote_identifier(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _quote_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _tag_column(table: PostGISTableInfo) -> ColumnInfo | None:
    for name in ("tags", "other_tags"):
        c = table.column_info(name)
        if c:
            return c
    return None


def _tag_value_expr(tag_col: ColumnInfo, key: str) -> tuple[str, bool]:
    """
    Return SQL expression for a tag key and whether it is structured.

    Structured examples:
      hstore: tags -> 'railway'
      jsonb:  tags ->> 'railway'

    Text fallback:
      tags::text
    """
    col = _quote_identifier(tag_col.name)
    data_type = (tag_col.data_type or "").lower()
    udt_name = (tag_col.udt_name or "").lower()

    if udt_name == "hstore" or data_type == "hstore":
        return f"({col} -> {_quote_literal(key)})", True

    if udt_name in {"json", "jsonb"} or data_type in {"json", "jsonb"}:
        return f"({col} ->> {_quote_literal(key)})", True

    return f"({col}::text)", False


def _build_column_predicate(table: PostGISTableInfo, term: Mapping[str, Any]) -> str | None:
    column_name = str(term.get("column") or "").strip()
    if not column_name:
        return None

    real_col = table.real_column_name(column_name)
    if not real_col:
        return None

    col = _quote_identifier(real_col)
    op = str(term.get("op") or "eq").lower()
    value = term.get("value")

    if op == "eq":
        return f"{col} = {_quote_literal(value)}"

    if op == "ne":
        return f"{col} <> {_quote_literal(value)}"

    if op == "in":
        values = _as_list(value)
        if not values:
            return None
        return f"{col} IN (" + ", ".join(_quote_literal(v) for v in values) + ")"

    if op == "ilike":
        values = _as_list(value)
        if not values:
            return None
        clauses = [f"{col} ILIKE {_quote_literal(v)}" for v in values]
        return "(" + " OR ".join(clauses) + ")"

    if op in {"exists", "is_not_null"}:
        return f"{col} IS NOT NULL"

    if op == "is_null":
        return f"{col} IS NULL"

    return None


def _build_tag_predicate(table: PostGISTableInfo, term: Mapping[str, Any]) -> str | None:
    key = str(term.get("tag") or "").strip()
    if not key:
        return None

    tag_col = _tag_column(table)
    if not tag_col:
        return None

    expr, structured = _tag_value_expr(tag_col, key)
    op = str(term.get("op") or "eq").lower()
    value = term.get("value")

    if structured:
        if op == "eq":
            return f"{expr} = {_quote_literal(value)}"

        if op == "ne":
            return f"{expr} <> {_quote_literal(value)}"

        if op == "in":
            values = _as_list(value)
            if not values:
                return None
            return f"{expr} IN (" + ", ".join(_quote_literal(v) for v in values) + ")"

        if op == "ilike":
            values = _as_list(value)
            if not values:
                return None
            clauses = [f"{expr} ILIKE {_quote_literal(v)}" for v in values]
            return "(" + " OR ".join(clauses) + ")"

        if op in {"exists", "is_not_null"}:
            return f"{expr} IS NOT NULL"

        if op == "is_null":
            return f"{expr} IS NULL"

    # Conservative text fallback for unknown tags format.
    # We intentionally avoid assuming a specific serialization.
    text_expr = expr
    if op in {"exists", "is_not_null"}:
        return f"{text_expr} ILIKE {_quote_literal('%' + key + '%')}"

    if op in {"eq", "ilike"}:
        values = _as_list(value)
        if not values:
            return None
        clauses = []
        for v in values:
            clauses.append(
                "("
                + f"{text_expr} ILIKE {_quote_literal('%' + key + '%')}"
                + " AND "
                + f"{text_expr} ILIKE {_quote_literal('%' + str(v) + '%')}"
                + ")"
            )
        return "(" + " OR ".join(clauses) + ")"

    if op == "in":
        values = _as_list(value)
        if not values:
            return None
        clauses = []
        for v in values:
            clauses.append(
                "("
                + f"{text_expr} ILIKE {_quote_literal('%' + key + '%')}"
                + " AND "
                + f"{text_expr} ILIKE {_quote_literal('%' + str(v) + '%')}"
                + ")"
            )
        return "(" + " OR ".join(clauses) + ")"

    return None


def _build_term_predicate(table: PostGISTableInfo, term: Mapping[str, Any]) -> str | None:
    if "column" in term:
        return _build_column_predicate(table, term)
    if "tag" in term:
        return _build_tag_predicate(table, term)
    return None


def build_safe_semantic_predicate(
    table: PostGISTableInfo,
    rule: Mapping[str, Any],
    *,
    include_geom_not_null: bool = True,
) -> PredicateBuildResult:
    """
    Build WHERE predicate from a semantic rule.

    Crucial behavior:
    - missing columns are skipped
    - tag fallback is used only if tags/other_tags exists
    - invalid terms do not produce invalid SQL
    - if no valid term remains, where=None
    """
    terms = list(rule.get("any") or [])

    clauses: list[str] = []
    used: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw in terms:
        if not isinstance(raw, Mapping):
            skipped.append({"term": raw, "reason": "term_not_mapping"})
            continue

        clause = _build_term_predicate(table, raw)
        if clause:
            clauses.append(clause)
            used.append(dict(raw))
        else:
            skipped.append({"term": dict(raw), "reason": "not_supported_by_table_schema"})

    if not clauses:
        return PredicateBuildResult(
            where=None,
            used_terms=tuple(used),
            skipped_terms=tuple(skipped),
        )

    geom_col = table.real_column_name(table.geom_col) or table.geom_col
    semantic = "(" + " OR ".join(clauses) + ")"

    if include_geom_not_null:
        where = f"{_quote_identifier(geom_col)} IS NOT NULL AND {semantic}"
    else:
        where = semantic

    return PredicateBuildResult(
        where=where,
        used_terms=tuple(used),
        skipped_terms=tuple(skipped),
    )


def _geometry_matches(table: PostGISTableInfo, preferences: Iterable[str]) -> bool:
    prefs = {_normalize_text(str(p)) for p in preferences}
    if not prefs:
        return True

    gtype = _normalize_text(table.geometry_type)

    if "geometry" in prefs:
        return True

    if "point" in prefs and "point" in gtype:
        return True

    if "line" in prefs and ("line" in gtype or "linestring" in gtype):
        return True

    # Many OSM polygon tables are registered as GEOMETRY, so GEOMETRY is accepted
    # for polygon preference.
    if "polygon" in prefs and ("polygon" in gtype or gtype == "geometry"):
        return True

    return False


def _table_preference_score(table: PostGISTableInfo, rule: Mapping[str, Any]) -> float:
    score = 0.0

    preferred = [str(v).lower() for v in rule.get("tables_preference") or []]
    if table.table.lower() in preferred or table.qualified_name.lower() in preferred:
        score += 100.0

    if _geometry_matches(table, rule.get("geometry_preference") or []):
        score += 30.0

    if table.table.lower().startswith("planet_osm_"):
        score += 5.0

    if table.estimated_rows:
        score += 1.0

    return score


def _candidate_columns(table: PostGISTableInfo, rule: Mapping[str, Any]) -> tuple[str, ...]:
    wanted: list[str] = []

    for common in ("osm_id", "id", "name", "type", "class", "category"):
        real = table.real_column_name(common)
        if real and real not in wanted and real != table.geom_col:
            wanted.append(real)

    for term in rule.get("any") or []:
        if not isinstance(term, Mapping):
            continue
        col = term.get("column")
        if not col:
            continue
        real = table.real_column_name(str(col))
        if real and real not in wanted and real != table.geom_col:
            wanted.append(real)

    return tuple(wanted[:24])


def resolve_semantic_layer(
    concept: str,
    schema_context: PostGISSchemaContext,
    *,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
    max_candidates: int = 5,
) -> list[SemanticLayerCandidate]:
    active_rules = rules or DEFAULT_SEMANTIC_RULES
    rule = active_rules.get(concept)
    if not rule:
        return []

    candidates: list[SemanticLayerCandidate] = []

    for table in schema_context.spatial_tables():
        if not _geometry_matches(table, rule.get("geometry_preference") or []):
            continue

        pred = build_safe_semantic_predicate(table, rule)
        if not pred.where:
            continue

        score = _table_preference_score(table, rule)
        score += len(pred.used_terms) * 10.0

        candidates.append(
            SemanticLayerCandidate(
                concept=concept,
                schema=table.schema,
                table=table.table,
                geom_col=table.geom_col,
                geometry_type=table.geometry_type,
                srid=table.srid,
                where=pred.where,
                columns=_candidate_columns(table, rule),
                score=score,
                used_terms=pred.used_terms,
                skipped_terms=pred.skipped_terms,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:max_candidates]


def infer_semantic_concepts(
    query: str,
    *,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    active_rules = rules or DEFAULT_SEMANTIC_RULES
    q = _normalize_text(query)

    found: list[str] = []

    for concept, rule in active_rules.items():
        labels: list[str] = []

        raw_labels = rule.get("labels")
        if isinstance(raw_labels, Mapping):
            labels.extend(str(x) for x in raw_labels.get("fa", []) or [])
            labels.extend(str(x) for x in raw_labels.get("en", []) or [])

        labels.extend(str(x) for x in rule.get("aliases", []) or [])

        for label in labels:
            if _normalize_text(label) and _normalize_text(label) in q:
                found.append(concept)
                break

    return found


def resolve_query_semantic_layers(
    query: str,
    schema_context: PostGISSchemaContext,
    *,
    explicit_concepts: Iterable[str] | None = None,
    rules: Mapping[str, Mapping[str, Any]] | None = None,
    max_candidates_per_concept: int = 5,
) -> dict[str, list[SemanticLayerCandidate]]:
    concepts = list(explicit_concepts or [])
    if not concepts:
        concepts = infer_semantic_concepts(query, rules=rules)

    resolved: dict[str, list[SemanticLayerCandidate]] = {}
    for concept in concepts:
        resolved[concept] = resolve_semantic_layer(
            concept,
            schema_context,
            rules=rules,
            max_candidates=max_candidates_per_concept,
        )

    return resolved


def discover_postgis_schema(
    conn: Any,
    *,
    include_columns: bool = True,
    include_row_estimates: bool = True,
    schemas: Iterable[str] | None = None,
) -> PostGISSchemaContext:
    """
    Discover PostGIS spatial tables using an existing psycopg2-like connection.
    The caller owns the connection lifecycle.
    """
    schema_filter = tuple(schemas or ())
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
          f_table_schema,
          f_table_name,
          f_geometry_column,
          type,
          srid
        FROM geometry_columns
        ORDER BY f_table_schema, f_table_name, f_geometry_column
        """
    )
    geom_rows = list(cur.fetchall())

    if schema_filter:
        geom_rows = [r for r in geom_rows if r[0] in schema_filter]

    columns_by_table: dict[tuple[str, str], list[ColumnInfo]] = {}
    estimates: dict[tuple[str, str], int] = {}

    if include_columns:
        cur.execute(
            """
            SELECT
              table_schema,
              table_name,
              column_name,
              data_type,
              udt_name
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position
            """
        )
        for schema, table, col, data_type, udt_name in cur.fetchall():
            if schema_filter and schema not in schema_filter:
                continue
            columns_by_table.setdefault((schema, table), []).append(
                ColumnInfo(
                    name=str(col),
                    data_type=str(data_type or ""),
                    udt_name=str(udt_name or ""),
                )
            )

    if include_row_estimates:
        try:
            cur.execute(
                """
                SELECT schemaname, relname, n_live_tup::bigint
                FROM pg_stat_user_tables
                """
            )
            for schema, table, count in cur.fetchall():
                if schema_filter and schema not in schema_filter:
                    continue
                estimates[(schema, table)] = int(count or 0)
        except Exception:
            estimates = {}

    tables: list[PostGISTableInfo] = []

    for schema, table, geom_col, geom_type, srid in geom_rows:
        tables.append(
            PostGISTableInfo(
                schema=str(schema),
                table=str(table),
                geom_col=str(geom_col),
                geometry_type=str(geom_type or ""),
                srid=int(srid) if srid is not None else None,
                columns=tuple(columns_by_table.get((schema, table), [])),
                estimated_rows=estimates.get((schema, table)),
            )
        )

    cur.close()
    return PostGISSchemaContext(tables=tuple(tables))


DEFAULT_SEMANTIC_RULES: dict[str, dict[str, Any]] = {
    "park": {
        "labels": {
            "fa": ["پارک", "بوستان", "فضای سبز"],
            "en": ["park", "green space"],
        },
        "geometry_preference": ["polygon", "point"],
        "tables_preference": ["planet_osm_polygon", "planet_osm_point"],
        "any": [
            {"column": "leisure", "op": "eq", "value": "park"},
            {"column": "landuse", "op": "eq", "value": "grass"},
            {"column": "landuse", "op": "eq", "value": "recreation_ground"},
            {"column": "name", "op": "ilike", "value": ["%پارک%", "%بوستان%", "%park%"]},
            {"tag": "leisure", "op": "eq", "value": "park"},
        ],
    },
    "hospital": {
        "labels": {
            "fa": ["بیمارستان", "درمانگاه", "مرکز درمانی"],
            "en": ["hospital", "clinic", "healthcare"],
        },
        "geometry_preference": ["polygon", "point"],
        "tables_preference": ["planet_osm_polygon", "planet_osm_point"],
        "any": [
            {"column": "amenity", "op": "in", "value": ["hospital", "clinic", "doctors"]},
            {"column": "healthcare", "op": "exists"},
            {"column": "name", "op": "ilike", "value": ["%بیمارستان%", "%درمانگاه%", "%hospital%", "%clinic%"]},
            {"tag": "amenity", "op": "in", "value": ["hospital", "clinic", "doctors"]},
            {"tag": "healthcare", "op": "exists"},
        ],
    },
    "school": {
        "labels": {
            "fa": ["مدرسه", "دبستان", "دبیرستان", "آموزشگاه"],
            "en": ["school", "primary school", "high school"],
        },
        "geometry_preference": ["polygon", "point"],
        "tables_preference": ["planet_osm_polygon", "planet_osm_point"],
        "any": [
            {"column": "amenity", "op": "in", "value": ["school", "kindergarten", "college", "university"]},
            {"column": "name", "op": "ilike", "value": ["%مدرسه%", "%دبستان%", "%دبیرستان%", "%school%"]},
            {"tag": "amenity", "op": "in", "value": ["school", "kindergarten", "college", "university"]},
        ],
    },
    "metro_station": {
        "labels": {
            "fa": ["ایستگاه مترو", "مترو", "ورودی مترو"],
            "en": ["metro station", "subway station", "metro entrance", "subway entrance"],
        },
        "geometry_preference": ["point"],
        "tables_preference": ["planet_osm_point"],
        "any": [
            {"column": "railway", "op": "eq", "value": "station"},
            {"column": "railway", "op": "eq", "value": "subway_entrance"},
            {"column": "public_transport", "op": "eq", "value": "station"},
            {"column": "station", "op": "eq", "value": "subway"},
            {"column": "name", "op": "ilike", "value": ["%مترو%", "%metro%", "%subway%"]},
            {"tag": "railway", "op": "eq", "value": "station"},
            {"tag": "railway", "op": "eq", "value": "subway_entrance"},
            {"tag": "station", "op": "eq", "value": "subway"},
            {"tag": "public_transport", "op": "eq", "value": "station"},
        ],
    },
    "shopping_center": {
        "labels": {
            "fa": ["مرکز خرید", "پاساژ", "مجتمع تجاری", "بازار"],
            "en": ["shopping center", "shopping centre", "shopping mall", "mall", "market"],
        },
        "geometry_preference": ["polygon", "point"],
        "tables_preference": ["planet_osm_polygon", "planet_osm_point"],
        "any": [
            {"column": "shop", "op": "eq", "value": "mall"},
            {"column": "amenity", "op": "eq", "value": "marketplace"},
            {"column": "building", "op": "eq", "value": "retail"},
            {"column": "landuse", "op": "eq", "value": "retail"},
            {"column": "name", "op": "ilike", "value": ["%مرکز خرید%", "%پاساژ%", "%بازار%", "%mall%", "%shopping%"]},
            {"tag": "shop", "op": "eq", "value": "mall"},
            {"tag": "amenity", "op": "eq", "value": "marketplace"},
            {"tag": "building", "op": "eq", "value": "retail"},
            {"tag": "landuse", "op": "eq", "value": "retail"},
        ],
    },
    "main_road": {
        "labels": {
            "fa": ["جاده اصلی", "خیابان اصلی", "بزرگراه", "معبر اصلی"],
            "en": ["main road", "major road", "highway"],
        },
        "geometry_preference": ["line"],
        "tables_preference": ["planet_osm_roads", "planet_osm_line"],
        "any": [
            {"column": "highway", "op": "in", "value": ["motorway", "trunk", "primary", "secondary"]},
            {"column": "name", "op": "ilike", "value": ["%بزرگراه%", "%اتوبان%", "%highway%"]},
            {"tag": "highway", "op": "in", "value": ["motorway", "trunk", "primary", "secondary"]},
        ],
    },
    "building": {
        "labels": {
            "fa": ["ساختمان", "بنا", "ملک"],
            "en": ["building", "property"],
        },
        "geometry_preference": ["polygon"],
        "tables_preference": ["planet_osm_polygon", "osm_tehran_buildings"],
        "any": [
            {"column": "building", "op": "exists"},
            {"tag": "building", "op": "exists"},
        ],
    },
}
