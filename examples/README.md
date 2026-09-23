# Examples

Runnable end to end. The code they demonstrate works from an installed
package (`pip install smart-spatial-system`), but the example files and
their sample data live in this repository - clone it, or download the
script plus the data folder it uses, to run them.

| I want to... | Start with |
|---|---|
| see the pipeline run offline, no LLM key | `accessibility_analysis.py`, `s3geo_accessibility_demo.py` |
| ask a plain-language question in one call | `s3geo_quickstart.py` |
| call the running API over HTTP | `query_via_http.py` |
| see a real-data demo on downloaded OSM layers | `urmia_real_estate_ranking.py` |
| see a complete study: data, both arms, metric, paper | [`istanbul_health_access/`](istanbul_health_access/README.md) |

## Scripts

| File | What it shows |
|---|---|
| `accessibility_analysis.py` | The library path: build a QuerySpec by rule, plan it as a DAG, execute it, print the ranking. No server, no LLM. |
| `s3geo_accessibility_demo.py` | The same ranking as `accessibility_analysis.py`, called entirely through `s3geo.<Name>` (`s3geo.DeterministicPlanner`, `s3geo.registry()`, `s3geo.DagExecutor`) instead of `orchestrator.*` - proving `import s3geo` alone reaches full manual control, not just `s3geo.query()`. No server, no LLM. This is the source recording for `docs/assets/demo.gif`. |
| `s3geo_quickstart.py` | The one-call library path: `s3geo.query("...", layers={...})` plans the operation chain from the question itself and executes it. Needs a real LLM key - see below. |
| `query_via_http.py` | The HTTP path: `/health`, `/query` with GeoJSON inputs, reading layers back, fetching the stored request. Standard library only. |
| `urmia_real_estate_ranking.py` | The one-call library path end to end, on real data: downloads OSM vector layers for Urmia (roads, transit stops, shopping centers), loads LLM settings from `.env`, then asks `s3geo.query()` three plain-language questions over the same layers and saves whatever each plan produces - a map layer, a ranked table, a PDF report. Needs a real LLM key and network access (LLM endpoint + Overpass API). |

```bash
python examples/accessibility_analysis.py
python examples/s3geo_accessibility_demo.py

# s3geo_quickstart.py and urmia_real_estate_ranking.py plan from natural
# language, so they need a real LLM key:
export LLM_API_KEY="..."          # or AVALAI_API_KEY / OPENAI_API_KEY
python examples/s3geo_quickstart.py
python examples/urmia_real_estate_ranking.py   # also downloads OSM data for Urmia

# For the HTTP example, start a server first:
smart-spatial-api serve --port 8000
python examples/query_via_http.py
```

## Case study: Istanbul health access

[`istanbul_health_access/`](istanbul_health_access/README.md) is a finished
research study, not a script. It asks which İstanbul neighbourhoods are
underserved by hospitals and clinics, using real OSM data (964 mahalle,
1,020 facilities). It answers twice, once with a hand-written plan and once
with `s3geo.query()` from plain language (N=20), and compares the two. At
0.5.6, 20/20 LLM-planned runs match the rule-based answer exactly.

It holds five numbered notebooks, the paper, the committed results and
figures, and the upstream bug and enhancement reports that drove releases
0.4.1 to 0.5.6. It has its own `requirements.txt` and is run from its own
folder. See its README.

## Sample data

`accessibility/` holds four small synthetic GeoJSON layers around Vienna -
five candidate sites plus metro stations, schools and parks. The
coordinates are real places, but the layers are hand-written samples for
the example, not a survey: they are there so the example runs offline
without downloading anything. Replace them with your own layers and adjust
the `AmenitySpec` list.

`fake_candidate_properties.geojson`, `fake_real_estate_all_layers.geojson`
and `real_estate_ranking_payload.fake.json` are the sample inputs for the
real-estate ranking workflow, as their names say.

`urmia_real_estate/` holds the two layers `urmia_real_estate_ranking.py`
can't get from OSM: `properties.geojson` (candidate parcels/apartments,
hand-written, anchored on real Urmia landmarks the same way
`accessibility/` is anchored on real Vienna places - not a real listings
feed) and `construction_zones.geojson` (an illustrative allowed-construction
polygon; zoning isn't reliably tagged in OSM). The other three layers -
`main_roads`, `transit_hubs`, `shopping_centers` - are downloaded for real
from the Overpass API into the git-ignored `osm_download/` on first run.
Urmia has no metro/subway, so `transit_hubs` stands in for the city's real
public-transit hubs (the interurban bus terminal and bus stops) - the
ranking formula's "metro" role is just the nearest major transit hub.
`output/` (git-ignored) is where the script saves each question's result.

`istanbul_health_access/data/processed/` is real OSM data (ODbL), unlike
the synthetic layers above; see that folder's `data/README.md`.
