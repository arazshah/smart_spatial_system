# data/

Local datasets live here and are **not** committed (see `.gitignore`).

The Tehran demo uses an OpenStreetMap extract loaded into PostGIS:

1. Download a Tehran (or Iran) extract in `.osm.pbf` format, for example from
   [Geofabrik](https://download.geofabrik.de/asia/iran.html) or
   [BBBike](https://download.bbbike.org/osm/bbbike/Tehran/), and save it as
   `data/osm/tehran.osm.pbf`.
2. Import it with osm2pgsql:
   ```bash
   createdb osm_tehran && osm2pgsql -d osm_tehran -U postgres --hstore data/osm/tehran.osm.pbf
   ```
3. Create the views the PostGIS connector expects:
   ```bash
   psql -d osm_tehran -f scripts/sql/osm_tehran_views.sql   # or osm_tehran_minimal_views.sql
   ```

`config/plugins/postgis_connector.yaml` points at this database.
