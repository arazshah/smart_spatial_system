# Package namespace collision risk (tracking doc for a future migration)

**Status:** known, documented, deliberately not fixed in 0.5.7. Tracked
here so a future session (or a GitHub issue created from this doc) doesn't
have to re-derive it. See also `docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md`,
which flagged the same issue at the "Open design questions" section when
`pip install .` packaging was first added, and deliberately deferred it.

## The problem

`pip install smart_spatial_system` (or `pip install .`) installs five
top-level packages into the environment's `site-packages`, as siblings:

| Installed top-level package | Source directory |
|---|---|
| `api` | `api/` |
| `config` | `config/` |
| `orchestrator` | `orchestrator/` |
| `plugins` | `plugins/` |
| `templates` | `templates/` |
| `smart_spatial_system` | `smart_spatial_system/` |
| `s3geo` | `s3geo/` |

`smart_spatial_system` and `s3geo` are distinctively named and effectively
collision-free. `api`, `config`, `orchestrator`, `plugins`, and `templates`
are generic, plausible names for **any** Python project to use for its own
code - a user's own application, another dependency, or a notebook's local
helper modules. Because Python resolves a bare `import config` (or `import
plugins`, etc.) by walking `sys.path` in order and returning the first
match, whichever package appears first on `sys.path` wins silently - there
is no error, no warning, just whichever one happened to be found first.
`tests/test_bug_008_package_namespace_collision.py` demonstrates this
concretely: a `config` package placed earlier on `sys.path` shadows this
project's `config` package entirely.

This is a real risk once this project is installed as a **library**
dependency (via `pip install smart_spatial_system`) into an environment
that also contains, or will later add, its own top-level `plugins/`,
`config/`, `orchestrator/`, `api/`, or `templates/` package - not merely a
theoretical one. It is not a risk for the primary, documented deployment
path (`Dockerfile` / `docker-compose.yml` / `requirements.lock`, which runs
this repository's own code directly, not as an installed dependency
alongside unrelated packages).

## Why it wasn't fixed in 0.5.7

The correct long-term fix is a namespace migration: rename these five
packages so they live under the `smart_spatial_system` namespace (e.g.
`smart_spatial_system.plugins`, `smart_spatial_system.orchestrator`, ...),
matching where the "real" application code already lives per this
project's own layering convention (see the root `CLAUDE.md`).

That is a real, repo-wide import-path migration, not a packaging-metadata
change:

- Every `from api.routers.x import y`, `from orchestrator.service import
  Z`, `from plugins.ndvi_calculator import calculate_ndvi`, etc. across the
  application code, the ~150 test modules under `tests/`, and any external
  callers (like the `s3geo` convenience wrapper) would need to change.
- Config file lookup (`plugins/_shared/plugin_config.py`) and any
  string/dotted-path plugin lookups would need re-auditing.
- It would be a breaking change for anyone already depending on the
  current flat import paths.

`docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md` explicitly scoped its first
packaging pass to ship the current flat layout as-is and recorded this as
an open design question rather than resolving it inline, to keep that
phase's diff reviewable. This release (0.5.7) is a bug-fix release for the
case-study findings, not a good place to bundle an unrelated repo-wide
rename, so the same call is made here: document it thoroughly, add a guard
test that demonstrates the risk concretely, and leave the actual migration
for a dedicated follow-up (this doc is meant to become that follow-up's
tracking issue).

## Migration plan (for when this is picked up)

1. Move `api/`, `config/`, `orchestrator/`, `plugins/`, `templates/` to
   live under `smart_spatial_system/` (e.g.
   `smart_spatial_system/plugins/ndvi_calculator.py`), or expose them as
   `smart_spatial_system.api` / `.plugins` / etc. via package aliasing.
2. Update every import site across the codebase (`api/`, `orchestrator/`,
   `plugins/`, `smart_spatial_system/`, `s3geo/`, `tests/`) to the new
   dotted path. A codemod (e.g. a scripted `libcst`/`ast`-based rewrite,
   not manual `sed`, given the volume) is recommended over hand-editing.
3. Ship thin backward-compatible shim packages at the old top-level names
   (`api`, `config`, `orchestrator`, `plugins`, `templates`) for at least
   one deprecation cycle, each just re-exporting from the new
   `smart_spatial_system.*` location and emitting a `DeprecationWarning` on
   import, so existing external callers (and any pinned `pip install`
   users) don't break immediately.
4. Update `[tool.setuptools.packages.find]` in `pyproject.toml` to reflect
   the new layout, and update `docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md`'s
   "Current package layout" table.
5. Remove the compatibility shims in a subsequent major/minor version once
   the deprecation window has passed.

## What 0.5.7 actually ships for this

- This document.
- `tests/test_bug_008_package_namespace_collision.py`, which concretely
  demonstrates a `config` package earlier on `sys.path` shadowing this
  project's `config` package, and asserts this document exists and names
  the affected packages.
- A `CHANGELOG.md` entry cross-referencing this doc.

No source code was renamed or moved as part of this release.
