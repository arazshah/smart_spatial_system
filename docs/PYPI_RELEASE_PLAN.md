# PyPI Release Plan

How to publish this project to PyPI, and what has to happen in what order.

## Status

Preparation done; **not yet published**. Everything a repository can do is
in place (license, metadata, release workflows). What remains are steps
that require the owner's PyPI account and cannot be done from a repository:
the one-time Trusted Publisher configuration and the tag that triggers a
release.

## The constraint that drives the whole order

`smart_spatial_system` depends on `geochat-sdk` and `geochat-kernel`, which
live in a separate repository (`arazshah/geochat-platform`) and are
currently declared in `pyproject.toml` as direct git references:

```
geochat-sdk @ git+https://github.com/arazshah/geochat-platform.git#subdirectory=geochat-sdk
```

**PyPI rejects any upload whose metadata contains a direct URL dependency**
(PEP 508 `name @ url` form). So this package cannot be published while
those references remain — and they can only be replaced with ordinary
version specifiers (`geochat-sdk>=1.0.0`) once those two packages are
themselves on PyPI.

Hence the order:

```
geochat-kernel  ->  geochat-sdk  ->  smart_spatial_system
```

**That order is kernel-first, and an earlier draft of this document had it
backwards.** Verified against the code rather than the declared metadata:
`geochat_sdk` imports `geochat_kernel` at module level in 13 places
(`decorators.py`, `plugin.py`, `types/raster.py`, `types/vector.py`),
while `geochat_kernel` contains no reference to `geochat_sdk` anywhere in
its source. The declared dependencies said the opposite — `geochat-kernel`
declared `geochat-sdk==1.0.0` and `geochat-sdk` declared only `pydantic` —
which meant `pip install geochat-sdk` would have installed a package that
raises `ModuleNotFoundError: geochat_kernel` on import. Both
`pyproject.toml` files were corrected (see arazshah/geochat-platform#1),
and a clean-virtualenv install now reports consistent versions and imports
successfully.

## Name availability

Checked (2026-09): `smart-spatial-system`, `smart_spatial_system`,
`geochat-sdk` and `geochat-kernel` all return 404 on PyPI, i.e. all four
names are free. Re-check immediately before publishing — names can be
taken at any time.

## What is already prepared

**`arazshah/geochat-platform`:**

- `LICENSE` (MIT) copied into both `geochat-sdk/` and `geochat-kernel/`, so
  each wheel carries its own license file.
- Complete PyPI metadata in both `pyproject.toml` files: SPDX `license`
  expression (PEP 639), `authors`, `keywords`, `classifiers`,
  `[project.urls]`.
- `.github/workflows/publish-kernel.yml` and
  `.github/workflows/publish-sdk.yml` — one per package (see the
  registration table below for why they cannot share a file), each
  publishing via Trusted Publishing.
- Verified locally: both build clean wheels with
  `Metadata-Version: 2.4`, `License-Expression: MIT`, and the LICENSE file
  inside `dist-info/licenses/`.

**`arazshah/smart_spatial_system`:**

- `LICENSE` (MIT, same copyright holder).
- PyPI metadata completed in `pyproject.toml` (license, authors, keywords,
  classifiers, project URLs) on top of the packaging work from
  `docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md`.
- `.github/workflows/publish.yml` — builds, then **fails the release if a
  direct URL dependency is still present in the built metadata**, then
  publishes via Trusted Publishing. This guard exists so step 4 below
  cannot be silently skipped.

## Release procedure

### Step 1 — Configure Trusted Publishing on PyPI (one time, owner only)

For each of the three projects, on pypi.org → Account settings →
Publishing → "Add a pending publisher" (a *pending* publisher is the right
choice while the project does not exist on PyPI yet):

| Project | Owner | Repository | Workflow | Environment |
|---|---|---|---|---|
| `geochat-kernel` | `arazshah` | `geochat-platform` | `publish-kernel.yml` | `pypi` |
| `geochat-sdk` | `arazshah` | `geochat-platform` | `publish-sdk.yml` | `pypi` |
| `smart_spatial_system` | `arazshah` | `smart_spatial_system` | `publish.yml` | `pypi` |

**The two geochat packages must use different workflow filenames.** PyPI
refuses to register two *pending* publishers sharing the same
(owner, repository, workflow, environment) configuration — it would have
no way to tell which not-yet-created project an OIDC token belongs to, and
rejects the second registration with *"A pending trusted publisher
matching this configuration has already been registered for a different
project name."* An earlier version of this plan had both pointing at a
single `publish.yml` and hit exactly that; `geochat-platform` now has one
workflow file per package.

No API token is created or stored anywhere — GitHub authenticates to PyPI
over OIDC. This is why the workflows request `id-token: write` and run in
an `environment: pypi`.

### Step 2 — Release `geochat-kernel` and `geochat-sdk`

In `arazshah/geochat-platform`, push a tag:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Both workflows trigger on the tag and run independently. Publish order is
deliberately **not** enforced between them, and does not need to be: PyPI
does not verify that a distribution's dependencies resolve at upload time.
The sdk -> kernel dependency only matters at install time, by which point
both uploads have finished.

Verify afterwards:

```bash
python -m venv /tmp/verify-geochat && source /tmp/verify-geochat/bin/activate
pip install geochat-sdk==1.0.0          # must pull geochat-kernel with it
python -c "import geochat_sdk, geochat_kernel; print(geochat_sdk.__version__, geochat_kernel.__version__)"
```

Both must print `1.0.0` — the runtime `__version__` and the distribution
version are kept in sync deliberately (a mismatch in the kernel was caught
in review before the first release).

### Step 3 — Consider a TestPyPI dry run first

Optional but recommended for a first-ever publish, because **a version
number on PyPI can never be reused**, even after deleting the release. A
mistake in 1.0.0 means burning the number and publishing 1.0.1. TestPyPI
(https://test.pypi.org) takes the same Trusted Publisher configuration and
lets the whole flow be rehearsed.

### Step 4 — Switch this package's dependencies, then release it

Only after step 2 has actually succeeded, in `pyproject.toml`:

```diff
-    "geochat-sdk @ git+https://github.com/arazshah/geochat-platform.git#subdirectory=geochat-sdk",
-    "geochat-kernel @ git+https://github.com/arazshah/geochat-platform.git#subdirectory=geochat-kernel",
+    "geochat-sdk>=1.0.0",
+    "geochat-kernel>=1.0.0",
```

Leave `requirements.txt` and `requirements.lock` as they are — those drive
the Docker image and CI, which install from source on purpose and are
unaffected by PyPI.

Then verify locally before tagging:

```bash
pip wheel . --no-deps -w /tmp/w     # then check the METADATA has no "@" in Requires-Dist
python -m pytest -q                 # still green
```

and release:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

### Step 5 — Verify the published package

In a clean virtualenv, from a directory that is *not* a checkout of this
repository (this also re-verifies the packaged-config fallback from
`docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md`):

```bash
python -m venv /tmp/verify-venv && source /tmp/verify-venv/bin/activate
pip install "smart_spatial_system[postgis,raster,pdf]"
smart-spatial-api serve --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/health
```

## Known limitation: editing plugin config in a pip-installed deployment

`find_config_dir()`'s packaged-defaults fallback is **read-only**:
`write_plugin_config()` refuses to write into it, because that directory
lives inside `site-packages` in a pip-installed deployment (see
`orchestrator/plugin_config_store.py::_require_writable_config_dir`).

So a pip-installed deployment that wants to edit plugin configuration
through `PUT /plugins/{plugin_id}/config` must point
`GEOCHAT_PLUGIN_CONFIG_DIR` at a writable directory, seeded from the
packaged defaults:

```bash
python -c "import importlib.resources as r, shutil; shutil.copytree(str(r.files('config')/'plugins'), '/etc/smart-spatial/plugins')"
export GEOCHAT_PLUGIN_CONFIG_DIR=/etc/smart-spatial/plugins
```

Source checkouts and the Docker image are unaffected — they resolve
`config/plugins/` from the working tree and stay writable as before.

**Possible follow-up** (deliberately not done yet, since it is a feature
rather than a packaging fix): seed a writable override directory under
`RuntimePaths` automatically on first write, and prefer it for reads too,
so a pip-installed deployment gets editable configuration with no manual
setup. That needs a per-file read fallback (override directory first,
packaged defaults second), otherwise a partially-populated override
directory would silently mask the packaged defaults for every plugin that
was never edited.

## Known gaps to address before a JOSS submission

Not blocking for PyPI, but they will be raised in a JOSS review:

- `geochat-platform` has **no tests and no CI workflow** at all. JOSS
  requires automated tests. `smart_spatial_system` is fine here
  (~1686 tests, CI runs pytest + ruff + a frontend build).
- Both `geochat-sdk` and `geochat-kernel` have very short READMEs; JOSS
  expects statement of need, installation, example usage, and community
  guidelines (contributing/issues/support).
- No `CITATION.cff` in either repository.
