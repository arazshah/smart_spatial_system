
ADR-003 — Multilingual Semantic Layer
Status

Accepted.

Context

Current usage is mostly Persian. User queries and many data fields are Persian. However, the system should become a product-grade geospatial intelligence platform and should support English and potentially other natural languages.

If semantic logic is hardcoded only around Persian terms, the system will not scale as a product. If it is hardcoded only around English, current Persian datasets and queries will suffer.

Decision

Internal semantic concepts must be language-neutral and canonical, preferably English-based identifiers.

Examples:

text
metro_station
shopping_center
park
hospital
school
road
building
property
risk_zone
zoning_area


Language-specific aliases must be separate from canonical concepts.

Example:

yaml
metro_station:
  en:
    - metro station
    - subway station
    - underground station
  fa:
    - ایستگاه مترو
    - مترو
    - متروی شهری

shopping_center:
  en:
    - shopping center
    - mall
    - commercial center
  fa:
    - مرکز خرید
    - مجتمع تجاری
    - پاساژ

Semantic Rules

The semantic layer should support:

query language detection when possible
canonical concept inference
multilingual aliases
data-value aliases
schema-aware resolution
source-aware semantic candidates
safe predicate generation
PostGIS Role

PostGIS semantic resolution is one implementation of source-aware semantic resolution.

It must not become the only semantic path.

Future resolvers may include:

WFS schema resolver
GeoPackage resolver
uploaded file schema resolver
API metadata resolver
catalog/metadata resolver
Product Language

Product documentation, public contracts, schema names, and internal concept IDs should be English.

User-facing responses may be localized using response_language.

Current default may remain Persian (fa) during development, but the architecture must support English-first productization.

Consequences

Positive:

Persian queries work now
English productization remains possible
data sources with Persian labels remain supported
semantic expansion becomes structured

Negative:

requires a concept/alias catalog
semantic resolver must avoid hardcoded one-off Persian patches