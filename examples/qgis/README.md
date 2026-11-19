# Using s3geo inside QGIS

Route 1: the Python library is called directly from QGIS' Python Console, on layers you already
have open. No server, no MCP, no API key for the deterministic path (`run`).

## 1. Install into the Python QGIS uses

s3geo needs Python >= 3.11. In the QGIS Python Console check `import sys; print(sys.version)`;
if it is older than 3.11, upgrade QGIS (3.34 LTR on current builds ships 3.12) or use route 2/3.

| OS | Command (run in the shell that sees QGIS' Python) |
|---|---|
| Windows | open **OSGeo4W Shell**, then `python -m pip install smart_spatial_system` |
| macOS | `/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install smart_spatial_system` |
| Linux (apt/flatpak QGIS) | `python3 -m venv --system-site-packages ~/s3env && ~/s3env/bin/pip install smart_spatial_system`, then in QGIS: `import sys; sys.path.insert(0, "/home/you/s3env/lib/python3.X/site-packages")` |

Installing `geopandas`/`shapely` next to QGIS' own GDAL can conflict on some setups; if the import of
`s3geo` fails after install, send the traceback (it is the first thing to check).

## 2. Run the demo

Open **Plugins > Python Console > Show Editor**, open `demo_in_qgis.py`, set `HERE`, press Run.
It loads two sample layers (3 homes, 2 Tehran metro stations), and adds `nearest_metro`,
`metro_500m` and `homes_within_500m` to the project. Expected console output:

```
2. home_A: 93 m  ... home_B: 143 m  ... home_C: 7707 m
4. homes within 500 m: ['home_A', 'home_B']
```

These numbers are what the same calls return outside QGIS (`tests/test_qgis_helper.py`).

## 3. Your own data

```python
from s3geo_qgis import run, ask, to_geojson, to_layer
lyr = iface.activeLayer()                        # or mapLayersByName("...")[0]
fc  = to_geojson(lyr, selected_only=True)        # always EPSG:4326
out = run("calculate_kernel_density", features=fc, metric=True)   # any of the 100 capabilities
to_layer(out, "density")
```

* `run(name, metric=True, ...)` reprojects to the UTM zone of the data, runs the capability,
  and returns EPSG:4326. Without `metric=True` a "500 m" buffer on lon/lat data is 500 **degrees**
  (the buffer now warns in `metadata["warnings"]`).
* Capability names and parameters: `python -c "import s3geo; print(s3geo.registry().registered_capability_names())"`
  or `docs/MCP_SERVER.md`.
* `ask("question", layer_a=fc_a, layer_b=fc_b)` plans with an LLM: set `LLM_API_KEY`
  (and `LLM_PROVIDER`/`LLM_BASE_URL`, see `.env.example`) in the environment before starting QGIS.
  It sends layer names, fields and extent to the provider.

## Status of this integration

`s3geo_qgis.py`'s analysis path is tested in CI without QGIS. `to_geojson` / `to_layer` use the
standard `QgsJsonExporter` / `QgsVectorLayer(..., "ogr")` calls and were **not run inside a real QGIS
here**; run the demo once and report anything that fails. Route 3 (a Processing Toolbox provider with
forms for each capability) is not built yet.
