CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS hstore;

DROP MATERIALIZED VIEW IF EXISTS osm_tehran_parks CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_buildings CASCADE;

CREATE MATERIALIZED VIEW osm_tehran_parks AS
SELECT
  osm_id,
  name,
  leisure,
  landuse,
  "natural",
  way,
  ST_Area(way) AS area_m2
FROM planet_osm_polygon
WHERE
  leisure IN ('park', 'garden')
  OR landuse IN ('grass', 'forest', 'recreation_ground')
  OR "natural" IN ('wood', 'grassland');

CREATE INDEX osm_tehran_parks_way_gix
ON osm_tehran_parks USING GIST (way);


CREATE MATERIALIZED VIEW osm_tehran_buildings AS
SELECT
  osm_id,
  name,
  building,
  way,
  ST_Area(way) AS area_m2,
  ST_PointOnSurface(way) AS centroid
FROM planet_osm_polygon
WHERE building IS NOT NULL;

CREATE INDEX osm_tehran_buildings_way_gix
ON osm_tehran_buildings USING GIST (way);

CREATE INDEX osm_tehran_buildings_centroid_gix
ON osm_tehran_buildings USING GIST (centroid);

ANALYZE osm_tehran_parks;
ANALYZE osm_tehran_buildings;
