# Phase 8 Plan — Backend Packaging and CLI

## Status

Planned, not started. This is a plan document only.

This phase number belongs to a different document than the one this
repo's other `PHASE*_PLAN.md` files track: `docs/REFACTOR_PLAN.md`'s own
phase numbering stops at Phase 7 (legacy cleanup) — it has no Phase 8.
"Phase 8" here is `docs/SMART_SPATIAL_SYSTEM_BACKEND_ROADMAP.md`'s and
`docs/ADR-004-service-oriented-modular-backend.md`'s phase numbering
(both name it "Backend Packaging and CLI" / "Backend Packaging + CLI"
identically), which is a separate, broader roadmap than REFACTOR_PLAN.md.
Don't confuse the two numbering schemes — REFACTOR_PLAN.md's Phase 7 and
this document's Phase 8 are unrelated phases from different plans that
happen to be adjacent numbers only within their own documents.

## Goal (per the ROADMAP/ADR-004 target)

```bash
pip install smart_spatial_system
smart-spatial-api serve --host 0.0.0.0 --port 8000
```

Make the backend installable and runnable as a standalone package,
independent of a git checkout.

## Headline finding: this is genuinely unstarted, and the checklist needs correcting against actual code

The ROADMAP's own Phase 8 checklist (12 items) reads as if the whole
thing is greenfield. Verifying against the actual repo shows a mixed
picture — some items are already satisfied as side effects of how mature
`api/main.py` already is, and one real, previously-undocumented gap
(config file resolution) would silently break the CLI target even if
everything else were done:

### Already effectively done, no Phase 8 work needed

- **Health endpoint**: `GET /health` already exists
  (`api/routers/system.py`), unauthenticated regardless of
  `SMART_SPATIAL_API_KEY`, documented in `docs/DEPLOYMENT.md`.
- **OpenAPI docs**: `api/main.py`'s `create_app()` builds a standard
  `FastAPI(...)` instance with no `docs_url`/`openapi_url` overrides, so
  `/docs` (Swagger UI) and `/openapi.json` are already live — this is a
  FastAPI default, not something Phase 8 needs to add.
- **Production-ready settings (partial)**: `APIConfig` (CORS origins),
  `SMART_SPATIAL_API_KEY` auth gating, `SMART_SPATIAL_RUNTIME_DIR`
  (`orchestrator/runtime_paths.py`) are all already externalized via env
  vars/constructor args, not hardcoded — see `docs/DEPLOYMENT.md`
  "Environment variables". The checklist's "Add environment/config
  loading" and "Add production-ready settings" items are largely already
  satisfied by this, not new work.
- **A working deployment path already exists** — just not the one this
  phase targets. `Dockerfile` + `docker-compose.yml` +
  `docs/DEPLOYMENT.md` are a mature, documented, CI-covered deployment
  mechanism (`requirements.lock`-based image build). Phase 8 is
  additive — a `pip install` path alongside Docker, not a replacement for
  it. Don't let Phase 8 work regress or duplicate the Docker path's
  dependency pinning (`requirements.lock`) or its documented env var
  contract.

### Genuinely not started (verified, not assumed)

- **No packaging metadata at all.** `pyproject.toml` exists but contains
  only `[tool.ruff]`/`[tool.ruff.lint]` sections (added for lint config,
  unrelated to packaging) — no `[build-system]`, no `[project]`. There is
  no `setup.py`/`setup.cfg` either. `pip install .` or `pip install
  smart_spatial_system` do not work today; nothing installs this repo as
  a package.
- **No CLI entrypoint.** No `console_scripts`/`entry_points`, no
  `__main__.py` anywhere in the repo (`api/`, `orchestrator/`, `plugins/`,
  `smart_spatial_system/` all checked), no `click`/`typer`/`argparse`
  usage found outside tests. The only documented way to start the server
  is `uvicorn api.main:app --reload` (README.md) or the Docker image's
  `CMD`. `smart-spatial-api serve ...` / `python -m smart_spatial_system
  serve ...` don't exist.
- **No dependency extras.** `requirements.txt` is one flat, unpinned
  list — geospatial deps (`rasterio`, `geopandas`, `psycopg[binary]`,
  `pyproj`), PDF rendering (`weasyprint`), and dev tooling (`pytest`,
  `ruff`) are not separated. The ROADMAP's target extras (`postgis`,
  `raster`, `dev`, `llm`) don't exist as groupings anywhere.

### A real gap the ROADMAP checklist doesn't call out: config-file resolution breaks once "installed and run from anywhere"

`orchestrator/plugin_config_store.py::find_config_dir()` (and its
`plugins/_shared/plugin_config.py` counterpart, same lookup order)
resolves `config/plugins/` by walking up from `Path.cwd()` looking for a
directory literally named `config/plugins`, unless
`GEOCHAT_PLUGIN_CONFIG_DIR` is set. This works today because the app is
always started from within a git checkout (`uvicorn api.main:app` from
the repo root, or the Docker image's `COPY . .` putting `config/` next to
the code). A `pip`-installed package invoked as `smart-spatial-api serve`
from an arbitrary directory has no such tree to find — confirmed by
reading `find_config_dir()` directly: with no env var set and no
`config/plugins` found anywhere from cwd up through every parent
directory, it returns `cwd / "config" / "plugins"` anyway, a path that
does not exist. Whatever reads from that returned `Path` next (per-plugin
config loaders) then either falls back to each plugin's own in-code
defaults (if it has any) or fails per that plugin's own error handling —
not a clean, uniform failure mode, and not exercised by any existing test
(every current test runs from within the git checkout, where the
cwd-walk always succeeds). This has to be solved (ship default configs
as package data with a resolvable path, and/or make
`GEOCHAT_PLUGIN_CONFIG_DIR` effectively required for a pip-installed run,
documented clearly) before "Test CLI command" in the ROADMAP checklist
can mean anything beyond "it starts", not "it actually loads plugin
configuration correctly."

## Current package layout (verified, not aspirational)

Four top-level Python packages, siblings, not nested under one namespace:

| Directory | Has `__init__.py` | Role |
|---|---|---|
| `api/` | yes | FastAPI app factory + routers |
| `orchestrator/` | yes | Planning, DAG, capability registry, service facade |
| `plugins/` | yes | ~36 geochat_sdk capability plugins |
| `smart_spatial_system/` | yes | Real application-service implementation (per CLAUDE.md's layering) |
| `config/` | no (data, not code) | YAML plugin configs, `plugin_state.json` |

This is the first open question a packaging pass has to answer: does the
installable distribution ship all four top-level packages under their
current names (so installing it puts `api`, `orchestrator`, `plugins` —
generic-sounding names — directly into the user's site-packages, with
real collision risk against other packages), or does packaging first
require consolidating them under one namespace (e.g. moving/aliasing to
`smart_spatial_system.api`, `smart_spatial_system.orchestrator`,
`smart_spatial_system.plugins`)? The second option is the "correct"
long-term shape for a published package but is a real, repo-wide import
path migration (every `from api.routers.x import y` / `from
orchestrator.service import Z` / plugin config module lookups by dotted
path), not a packaging-config change — out of scope for a first Phase 8
pass. See "Open design questions" below.

## Non-goals for this phase (mirrors this repo's pattern for phases with a large adjacent design space)

- No repo-wide package rename/restructure (`api` → `smart_spatial_system.api` etc.) in a first pass — that's a real migration, not a packaging-metadata change. Ship the current flat top-level layout as-is unless/until that's explicitly decided.
- No change to the Docker deployment path — `Dockerfile`/`docker-compose.yml`/`requirements.lock` stay the primary, documented, CI-covered path; pip packaging is additive.
- No multi-tenant/auth redesign — out of scope, unrelated to packaging (see `docs/DEPLOYMENT.md`'s "Deployment model").
- No new runtime features — this phase is packaging/distribution only, not new API surface.
- No PyPI publish automation (CI workflow, trusted publishing, version bump tooling) in a first pass — get `pip install .` / `pip install -e .` working and verified locally first; publishing infrastructure is a follow-on once the package itself is correct.

## Open design questions this plan does not resolve

Recorded here, same pattern as Phase 6's plan, so a future session
doesn't have to re-derive them:

1. **Package/import-path shape** (above): ship flat top-level packages
   now, or require the namespace migration first? This is the single
   biggest scope decision — it changes whether Phase 8 is "add
   `pyproject.toml` packaging metadata" (small) or "rename every
   top-level import across ~150 test files and the whole codebase, then
   add packaging metadata" (large).
2. **Config file distribution**: ship `config/plugins/*.yaml` (and
   `*.example.yaml` templates) as package data with an
   `importlib.resources`-based default lookup, or keep the
   cwd-walk-then-env-var behavior and simply document that
   `GEOCHAT_PLUGIN_CONFIG_DIR` (or an equivalent CLI flag, e.g.
   `smart-spatial-api serve --config-dir ...`) is required for a
   pip-installed run? The current plugins ship real default YAML (not
   just examples) — e.g. `config/plugins/local_vector_loader.yaml` — so
   "no config dir found" isn't just a PostGIS-credentials problem, it can
   silently degrade plugins that are supposed to always be available.
3. **What counts as `smart-spatial-api`'s scope**: just `serve` (start
   the FastAPI app, per the ROADMAP's literal target), or also
   thin CLI wrappers for existing one-off operational scripts
   (`requirements.lock` regeneration is documented as a manual venv
   dance in CLAUDE.md; `scripts/sql/` exists for PostGIS demo data per
   `data/README.md`) — bundling those under one CLI is a nice-to-have,
   not implied by the ROADMAP's literal checklist, and adds scope.
4. **Extras granularity**: the ROADMAP names four extras
   (`postgis`, `raster`, `dev`, `llm`) but today's `requirements.txt` has
   no LLM-specific dependency at all (the OpenAI-compatible client in
   `orchestrator/` uses plain `httpx`, already a base dependency) — is
   `llm` actually an extra with distinct deps, or does the ROADMAP's
   list need correcting once this is worked through in detail?

## Migration steps (small, independently testable, in order)

Steps 1-2 are prerequisite investigation/decisions; steps 3+ are
mechanical once those land. Do not skip straight to step 3 without an
explicit answer to open questions 1-2 above — that's exactly the kind of
"quick pass that turns out to need a redesign halfway through" this
repo's conventions (CLAUDE.md, REFACTOR_PLAN.md) warn against.

1. **Resolve open question 1** (package/import-path shape) as its own
   decision, informed by this document, before writing any packaging
   config. If the answer is "flat layout for now", document that
   explicitly as the chosen scope (not a default fallen into) and note
   the future migration as deferred. If the answer is "migrate first",
   that becomes its own prerequisite phase/PR — do not bundle a
   repo-wide import rename into the same PR as packaging metadata.
2. **Resolve open question 2** (config file distribution) and implement
   whichever path is chosen, with a test that actually installs the
   package into a clean virtualenv (or an equivalent isolated check) and
   confirms plugin config loads correctly from outside the git checkout
   — not just that `find_config_dir()`'s unit tests still pass unchanged
   in-repo, since those don't exercise the "run from an arbitrary cwd"
   case that matters here.
3. **Add real packaging metadata to `pyproject.toml`**: `[build-system]`
   (`setuptools` + `wheel` is the least-surprising choice given no other
   build backend is in use anywhere in this repo), `[project]` (name,
   version, description, `requires-python = ">=3.11"` per
   `tool.ruff.target-version`), dependencies split from
   `requirements.txt` into `dependencies` (base) +
   `[project.optional-dependencies]` for the extras decided in open
   question 4. Keep `requirements.txt`/`requirements.lock` as the
   Docker-image source of truth (per CLAUDE.md, don't let this diverge
   silently — document which file is authoritative for which deployment
   path).
4. **Add the CLI entrypoint**: a new `smart_spatial_system/cli.py` (or
   wherever open question 1 says import paths should live) with a
   `serve` subcommand that wraps `uvicorn.run("api.main:app", host=...,
   port=..., ...)` — mirroring, not reimplementing, what
   `uvicorn api.main:app --reload` already does today. Register it as a
   `console_scripts` entry point (`smart-spatial-api`) in
   `[project.scripts]`. Also verify/add `python -m smart_spatial_system
   serve ...` (a `smart_spatial_system/__main__.py`) since the ROADMAP
   names both forms as targets.
5. **Test `pip install` in a clean virtualenv** (the ROADMAP's own exit
   criterion): a fresh venv, `pip install .` (or `pip install -e .` for
   the dev loop), confirm the package installs, `smart-spatial-api serve
   --host 127.0.0.1 --port 8000` starts, `/health` and `/docs` respond.
   This is the actual gate for "done", not just "packaging config exists
   and imports don't error."
6. **Update docs**: `README.md`'s quick-start gets the new `pip install`
   path alongside (not replacing) the existing `uvicorn api.main:app
   --reload` dev instructions; `docs/DEPLOYMENT.md` gets a note on
   whether/how this relates to the Docker path (most likely: pip install
   is for local/dev or embedding in another Python project, Docker stays
   recommended for production, given the auth model's "one deployer"
   framing already documented there).

## Non-goals recap for what NOT to attempt in one pass

Per this plan's own steps: if open question 1 resolves to "migrate
import paths first", that migration is its own effort with its own plan
document (mirroring how Phase 6 explicitly separated small fixes from
ADR-004's from-scratch connector-registry design) — don't let "plan
Phase 8" silently become "also execute a repo-wide rename" without a
dedicated decision point.

## Testing strategy

- Steps 1-2 (config resolution): a test that simulates "no `config/`
  tree reachable from cwd" (e.g. run from a genuinely isolated tmp
  directory) and confirms the chosen fallback (package data or a clear,
  documented error) actually works, not just that in-repo behavior is
  unchanged.
- Step 3 (packaging metadata): no new runtime tests — verified by the
  install itself (step 5) succeeding, plus confirming
  `requirements.txt`/`requirements.lock`-based installs (existing CI,
  Docker build) are unaffected by the added `pyproject.toml` sections.
- Step 4 (CLI): a smoke test invoking the CLI entrypoint (subprocess or
  direct function call to the `serve` implementation with
  immediate-shutdown/dry-run semantics, not a long-running server in the
  test suite) confirming it constructs the same `app` `uvicorn
  api.main:app` would.
- Step 5 (clean-venv install): not automatable as a fast unit test in
  this repo's existing `pytest` suite — do it manually per this plan's
  step 5, and consider a dedicated CI job (separate from the main
  `requirements.lock`-based test job) if this needs to stay verified
  over time rather than checked once.
- Full existing suite (`pytest`) must stay green throughout — none of
  these steps should change any existing runtime behavior for the
  Docker/dev-checkout path.

## Suggested commit breakdown

1. Decision + doc update for open questions 1-2 (no code yet) — small,
   review-friendly, unblocks everything else.
2. Config-file distribution fix (step 2) — independent of packaging
   metadata, testable on its own.
3. `pyproject.toml` packaging metadata + extras (step 3) — one commit.
4. CLI entrypoint + `__main__.py` (step 4) — one commit, depends on 3.
5. Clean-venv install verification + doc updates (steps 5-6) — closes
   the phase, depends on 3-4.

Each is independently reviewable and revertible, consistent with every
other phase in this repo.
