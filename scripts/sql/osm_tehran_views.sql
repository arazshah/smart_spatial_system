CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS hstore;

DROP MATERIALIZED VIEW IF EXISTS osm_tehran_metro_stations CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_main_roads CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_hospitals CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_schools CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_parks CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_shopping_centers CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_buildings CASCADE;
DROP MATERIALIZED VIEW IF EXISTS osm_tehran_pois CASCADE;

-- Metro/subway stations
CREATE MATERIALIZED VIEW osm_tehran_metro_stations AS
SELECT
  osm_id,
  name,
  railway,
  public_transport,
  tags,
  way
FROM planet_osm_point
WHERE
  railway IN ('station', 'halt')
  AND (
    COALESCE(tags -> 'station', '') = 'subway'
    OR COALESCE(tags -> 'subway', '') = 'yes'
    OR COALESCE(tags -> 'network', '') ILIKE '%metro%'
    OR COALESCE(name, '') ILIKE '%metro%'
    OR COALESCE(name, '') ILIKE '%مترو%'
  );

CREATE INDEX osm_tehran_metro_stations_way_gix
ON osm_tehran_metro_stations USING GIST (way);


-- Main roads
CREATE MATERIALIZED VIEW osm_tehran_main_roads AS
SELECT
  osm_id,
  name,
  highway,
  tags,
  way
FROM planet_osm_line
WHERE highway IN (
  'motorway',
  'trunk',
  'primary',
  'secondary',
  'tertiary',
  'motorway_link',
  'trunk_link',
  'primary_link',
  'secondary_link',
  'tertiary_link'
);

CREATE INDEX osm_tehran_main_roads_way_gix
ON osm_tehran_main_roads USING GIST (way);


-- Hospitals / clinics
CREATE MATERIALIZED VIEW osm_tehran_hospitals AS
SELECT
  osm_id,
  name,
  amenity,
  tags,
  way
FROM planet_osm_point
WHERE
  amenity IN ('hospital', 'clinic', 'doctors')
  OR COALESCE(tags -> 'healthcare', '') IN ('hospital', 'clinic', 'doctor');

CREATE INDEX osm_tehran_hospitals_way_gix
ON osm_tehran_hospitals USING GIST (way);


-- Schools / universities / kindergartens
CREATE MATERIALIZED VIEW osm_tehran_schools AS
SELECT
  osm_id,
  name,
  amenity,
  tags,
  way
FROM planet_osm_point
WHERE amenity IN ('school', 'university', 'college', 'kindergarten');

CREATE INDEX osm_tehran_schools_way_gix
ON osm_tehran_schools USING GIST (way);


-- Parks / green areas
CREATE MATERIALIZED VIEW osm_tehran_parks AS
SELECT
  osm_id,
  name,
  leisure,
  landuse,
  "natural",
  tags,
  way,
  ST_Area(way) AS area_m2
FROM planet_osm_polygon
WHERE
  leisure IN ('park', 'garden')
  OR landuse IN ('grass', 'forest', 'recreation_ground')
  OR "natural" IN ('wood', 'grassland');

CREATE INDEX osm_tehran_parks_way_gix
ON osm_tehran_parks USING GIST (way);


-- Shopping centers as points and polygon point-on-surface
CREATE MATERIALIZED VIEW osm_tehran_shopping_centers AS
SELECT
  osm_id,
  name,
  shop,
  amenity,
  tags,
  way
FROM planet_osm_point
WHERE
  shop IN ('mall', 'supermarket', 'department_store')
  OR amenity IN ('marketplace')
  OR COALESCE(tags -> 'building', '') = 'retail'

UNION ALL

SELECT
  osm_id,
  name,
  shop,
  amenity,
  tags,
  ST_PointOnSurface(way) AS way
FROM planet_osm_polygon
WHERE
  shop IN ('mall', 'supermarket', 'department_store')
  OR amenity IN ('marketplace')
  OR COALESCE(tags -> 'building', '') = 'retail';

CREATE INDEX osm_tehran_shopping_centers_way_gix
ON osm_tehran_shopping_centers USING GIST (way);


-- Buildings
CREATE MATERIALIZED VIEW osm_tehran_buildings AS
SELECT
  osm_id,
  name,
  building,
  tags,
  way,
  ST_Area(way) AS area_m2,
  ST_PointOnSurface(way) AS centroid
FROM planet_osm_polygon
WHERE building IS NOT NULL;

CREATE INDEX osm_tehran_buildings_way_gix
ON osm_tehran_buildings USING GIST (way);

CREATE INDEX osm_tehran_buildings_centroid_gix
ON osm_tehran_buildings USING GIST (centroid);


-- General POIs, unified point geometry
CREATE MATERIALIZED VIEW osm_tehran_pois AS
SELECT
  osm_id,
  name,
  amenity,
  shop,
  tourism,
  leisure,
  tags,
  way,
  'point' AS source_geometry
FROM planet_osm_point
WHERE
  amenity IS NOT NULL
  OR shop IS NOT NULL
  OR tourism IS NOT NULL
  OR leisure IS NOT NULL

UNION ALL

SELECT
  osm_id,
  name,
  amenity,
  shop,
  tourism,
  leisure,
  tags,
  ST_PointOnSurface(way) AS way,
  'polygon_centroid' AS source_geometry
FROM planet_osm_polygon
WHERE
  amenity IS NOT NULL
  OR shop IS NOT NULL
  OR tourism IS NOT NULL
  OR leisure IS NOT NULL;

CREATE INDEX osm_tehran_pois_way_gix
ON osm_tehran_pois USING GIST (way);


ANALYZE planet_osm_point;
ANALYZE planet_osm_line;
ANALYZE planet_osm_polygon;
ANALYZE planet_osm_roads;

ANALYZE osm_tehran_metro_stations;
ANALYZE osm_tehran_main_roads;
ANALYZE osm_tehran_hospitals;
ANALYZE osm_tehran_schools;
ANALYZE osm_tehran_parks;
ANALYZE osm_tehran_shopping_centers;
ANALYZE osm_tehran_buildings;
ANALYZE osm_tehran_pois;
