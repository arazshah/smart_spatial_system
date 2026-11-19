# S3Geo QGIS plugin

`qgis_plugin/s3geo_processing/` adds the Smart Spatial System tools to the QGIS **Processing
Toolbox** (group *S3Geo*). It is a thin form layer: every algorithm calls a plugin capability
through `s3geo.registry()`, the same code the API and the MCP server use (ADR-001).

| Algorithm | Capability | Notes |
|---|---|---|
| Nearest feature (distance in metres) | `find_nearest_neighbors` | `_nearest_distance` in m, target attributes as JSON |
| Buffer (distance in metres) | `buffer_vector_features` | metres whatever the layer CRS |
| Area and perimeter | `calculate_area_perimeter` | `_area` m², `_perimeter` m |
| Points inside polygons | `filter_points_in_polygon` | |
| Hotspot analysis (Gi*) | `calculate_hotspots` | numeric field + K neighbours |
| Clusters (DBSCAN) | `cluster_features` | EPS in metres |
| Run any capability (advanced) | any of the 100 | JSON parameters, `"@A"`/`"@B"` stand for the layers |
| Ask a question (experimental) | `s3geo.query` | needs an LLM key; sends layer names/fields/extent to the provider |

Metric algorithms reproject the inputs to the UTM zone of the data, analyse, and return the result
in EPSG:4326.

## Install (users)

The plugin needs the Python package **in the Python that QGIS runs**; the plugin itself is only
the forms. Check which Python that is: QGIS > Plugins > Python Console:
`import sys; print(sys.executable, sys.version)` (needs 3.11+).

### Linux (tested: Ubuntu 24.04, `apt install qgis python3-qgis`, QGIS 3.34, Python 3.12)

```bash
python3 -m venv --system-site-packages ~/s3env
~/s3env/bin/pip install smart_spatial_system
~/s3env/bin/pip install "numpy<2" "pandas<3" "geopandas<1.1"
```

The last line matters: pip otherwise installs numpy 2, which breaks the system GDAL/SciPy that QGIS
loads (`_ARRAY_API not found` in `osgeo.gdal_array`). `pip install` straight into the system Python
is refused by Ubuntu (PEP 668), hence the venv.

QGIS does not use the venv by itself; point its Python at it, either of:

```bash
# A) launcher
PYTHONPATH=$HOME/s3env/lib/python3.12/site-packages qgis

# B) permanent: QGIS > Settings > User Profiles > Open Active Profile Folder, then create
#    python/startup.py containing:
#    import sys; sys.path.insert(0, "/home/YOU/s3env/lib/python3.12/site-packages")
```

Flatpak/snap QGIS has its own Python and cannot see `~/s3env`; use the apt/PPA build or install
into the Flatpak's Python.

### Windows / macOS

Windows: OSGeo4W Shell, `python -m pip install -U smart_spatial_system`.
macOS: `/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install smart_spatial_system`.

### The plugin

Until it is in the official repository: download `s3geo_processing-<version>.zip` from the
GitHub release (or build it with `python scripts/package_qgis_plugin.py`), then QGIS >
Plugins > Manage and Install Plugins > **Install from ZIP**. The plugin is flagged experimental:
tick *Settings > Show also experimental plugins*. Menu Plugins > S3Geo > *check installation*
reports the package version and capability count.

## Develop / test

```bash
# Linux, QGIS from apt
sudo apt install python3-qgis qgis-providers
python3 -m venv --system-site-packages ~/s3env && ~/s3env/bin/pip install -e . pytest
~/s3env/bin/pip install "numpy<2" "pandas<3" "geopandas<1.1"
QT_QPA_PLATFORM=offscreen ~/s3env/bin/python -m pytest tests/test_qgis_plugin_processing.py
python scripts/package_qgis_plugin.py          # dist/s3geo_processing-<version>.zip
```

`tests/test_qgis_plugin_package.py` (metadata, zip layout, plain-Python helpers) runs in the normal
CI; `tests/test_qgis_plugin_processing.py` needs PyQGIS and is skipped without it.

## Publish

**1. In this repository (GitHub).** Merge the PR. For a plugin release, bump `version` and
`changelog` in `qgis_plugin/s3geo_processing/metadata.txt`, replace the placeholder `email`, then
run the workflow *QGIS plugin* (Actions tab > Run workflow, `release: true`) or push a tag
`qgis-plugin-vX.Y.Z`: it validates, builds the zip, and for tags attaches it to a GitHub release.
The plugin version is independent of the Python package version; the plugin requires the package
`>= 0.6.2` (`MIN_PACKAGE_VERSION` in `core.py`).

**2. Official QGIS plugin repository (plugins.qgis.org).** Done by the owner, by hand:

1. Create an OSGeo account (https://www.osgeo.org/community/getting-started-osgeo/) and sign in
   at https://plugins.qgis.org.
2. *Plugins > Upload a plugin*, upload the zip built with `--release`.
3. The first upload is reviewed by the repository staff (approval can take days). Requirements they
   check: GPL-compatible open licence (MIT is fine; LICENSE is in the zip), valid
   `metadata.txt` (about, tracker, repository, homepage, author, email), no compiled binaries,
   no code that downloads and executes code, a sensible `description`.
4. Later uploads of a higher `version` go live after the same checks (usually faster); keep
   `experimental=True` until the plugin has been used on real data.
5. Optional automation: `qgis-plugin-ci release` can upload with your OSGeo credentials stored as
   repository secrets; not wired up here.

**3. Your own repository (no review).** Host a `plugins.xml` (e.g. under docs-site/ on
s3geo.com) that points to the zip; users add it in *Plugins > Settings > Plugin Repositories*.
