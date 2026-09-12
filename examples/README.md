# Examples

Runnable end to end. The code they demonstrate works from an installed
package (`pip install smart-spatial-system`), but the example files and
their sample data live in this repository - clone it, or download the two
scripts and the `accessibility/` folder, to run them.

| File | What it shows |
|---|---|
| `accessibility_analysis.py` | The library path: build a QuerySpec by rule, plan it as a DAG, execute it, print the ranking. No server, no LLM. |
| `query_via_http.py` | The HTTP path: `/health`, `/query` with GeoJSON inputs, reading layers back, fetching the stored request. Standard library only. |

```bash
python examples/accessibility_analysis.py

# For the HTTP example, start a server first:
smart-spatial-api serve --port 8000
python examples/query_via_http.py
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
