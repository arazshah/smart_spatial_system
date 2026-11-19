"""
Paste into QGIS: Plugins > Python Console > Show Editor > open this file > Run.
Set HERE to this folder first (the one holding s3geo_qgis.py and data/).
Adds 3 result layers to the open project; each step prints what it did.
"""
import sys

from qgis.core import QgsProject, QgsVectorLayer

HERE = r"C:\path\to\smart_spatial_system\examples\qgis"      # <- edit (Linux/macOS: "/path/to/...")
sys.path.insert(0, HERE)
import s3geo_qgis as s3  # noqa: E402

# 1. Load the sample layers (or use your own: QgsProject.instance().mapLayersByName("..."))
for name in ("homes", "metro"):
    lyr = QgsVectorLayer(f"{HERE}/data/{name}.geojson", name, "ogr")
    assert lyr.isValid(), name
    QgsProject.instance().addMapLayer(lyr)
homes = s3.to_geojson(QgsProject.instance().mapLayersByName("homes")[0])
metro = s3.to_geojson(QgsProject.instance().mapLayersByName("metro")[0])
print("1. loaded", len(homes["features"]), "homes and", len(metro["features"]), "stations")

# 2. Distance from every home to the nearest metro station, in METRES (UTM chosen automatically)
near = s3.run("find_nearest_neighbors", metric=True, source_features=homes, target_features=metro, k=1)
for f in near["features"]:
    p = f["properties"]
    print(f"2. {p['name']}: {p['_nearest_distance']:.0f} m  (computed in {near['metadata'].get('computed_in_crs')})")
s3.to_layer(near, "nearest_metro")

# 3. 500 m catchment around each station
buf = s3.run("buffer_vector_features", metric=True, features=metro, distance=500, units="meters")
s3.to_layer(buf, "metro_500m")
print("3. buffers added")

# 4. Homes inside the catchment
inside = s3.run("filter_points_in_polygon", metric=False, points=homes, polygons=buf)
s3.to_layer(inside, "homes_within_500m")
print("4. homes within 500 m:", [f["properties"]["name"] for f in inside["features"]])
