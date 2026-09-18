# Examples

Runnable end to end. The code they demonstrate works from an installed
package (`pip install smart-spatial-system`), but the example files and
their sample data live in this repository - clone it, or download the two
scripts and the `accessibility/` folder, to run them.

| File | What it shows |
|---|---|
| `accessibility_analysis.py` | The library path: build a QuerySpec by rule, plan it as a DAG, execute it, print the ranking. No server, no LLM. |
| `s3geo_quickstart.py` | The one-call library path: `s3geo.query("...", layers={...})` plans the operation chain from the question itself and executes it. Needs a real LLM key - see below. |
| `query_via_http.py` | The HTTP path: `/health`, `/query` with GeoJSON inputs, reading layers back, fetching the stored request. Standard library only. |
| `urmia_real_estate_ranking.py` | The HTTP path, end to end on a real-estate ranking workflow: five named vector layers (candidate properties, transit hubs, shopping centers, main roads, allowed construction zones) in, a scored/ranked map layer, a table, and a downloadable PDF report out. Standard library only, no LLM key needed. |

```bash
python examples/accessibility_analysis.py

# s3geo_quickstart.py plans from natural language, so it needs a real LLM key:
export LLM_API_KEY="..."          # or AVALAI_API_KEY / OPENAI_API_KEY
python examples/s3geo_quickstart.py

# For the HTTP examples, start a server first:
smart-spatial-api serve --port 8000
python examples/query_via_http.py
python examples/urmia_real_estate_ranking.py
```

## Data

`accessibility/` holds four small synthetic GeoJSON layers around Vienna -
five candidate sites plus metro stations, schools and parks. The
coordinates are real places, but the layers are hand-written samples for
the example, not a survey: they are there so the example runs offline
without downloading anything. Replace them with your own layers and adjust
the `AmenitySpec` list.

`fake_candidate_properties.geojson`, `fake_real_estate_all_layers.geojson`
and `real_estate_ranking_payload.fake.json` are the sample inputs for the
real-estate ranking workflow, as their names say.

`urmia_real_estate/` holds the five layers for `urmia_real_estate_ranking.py`
(`properties`, `metro_stations`, `shopping_centers`, `main_roads`,
`construction_zones`), anchored on real Urmia landmarks and boulevards the
same way `accessibility/` is anchored on real Vienna places: real names and
approximate coordinates, hand-written for the example rather than surveyed
or fetched from OSM, so it runs fully offline. Urmia has no metro/subway,
so `metro_stations` stands in for the city's real public-transit hubs (the
interurban bus terminal and BRT/bus stops) - the ranking formula's "metro"
role is just the nearest major transit hub. `output/` (git-ignored) is
where the script writes the ranked GeoJSON layer and the downloaded PDF.
