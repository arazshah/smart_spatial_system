# Phase 6 / Step 2 — Runtime Directory Standardization

## Status

Closed.

## Purpose

The purpose of this step was to define and introduce a standard runtime
directory layout for generated backend state.

Before this step, the backend used several root-level runtime directories by
default:

```text
outputs/
uploads/
projects/
cache/
artifacts/reports/
output/


This made the repository harder to audit and mixed source code with generated
runtime state.

Phase 6 requires cleaner repository and architecture boundaries before deeper
backend decomposition. Runtime state must be separated from source code before
extracting more internal services.

Decision

The backend now has a canonical runtime path abstraction:

text
orchestrator/runtime_paths.py


The default local runtime root is:

text
var/


The environment variable for overriding it is:

text
SMART_SPATIAL_RUNTIME_DIR


The standard runtime layout is:

text
var/
  outputs/
  uploads/
  projects/
  reports/
  cache/

RuntimePaths

A new RuntimePaths helper was added.

Module:

text
orchestrator/runtime_paths.py


Main capabilities:

text
RuntimePaths.from_root(...)
RuntimePaths.from_env(...)
RuntimePaths.ensure()
RuntimePaths.as_dict()


Path resolution priority:

text
1. explicit runtime_dir argument
2. SMART_SPATIAL_RUNTIME_DIR
3. default runtime root: var


Important behavior:

text
RuntimePaths does not create directories unless ensure() is called.


This keeps service initialization side-effect light.

OrchestratorService Integration

OrchestratorServiceConfig now supports:

text
runtime_dir
outputs_path
uploads_path
projects_path


The storage path resolution behavior is:

text
If outputs_path/uploads_path/projects_path are explicitly provided:
    use those explicit paths.

Otherwise:
    use RuntimePaths derived from runtime_dir, SMART_SPATIAL_RUNTIME_DIR, or var.


This preserves backward compatibility for tests and callers that already pass
explicit storage paths.

Effective Storage Paths

The following internal storage components are now wired through effective runtime
paths when explicit paths are not provided:

text
OutputStorage  -> runtime_paths.outputs
UploadStorage  -> runtime_paths.uploads
ProjectStore   -> runtime_paths.projects


The storage classes themselves still accept direct root_dir configuration and
remain independently testable.

Health and Runtime Settings Metadata

Runtime path metadata is now exposed through service metadata.

get_health()

OrchestratorService.get_health() now includes:

json
{
  "runtime_paths": {
    "root": "...",
    "outputs": "...",
    "uploads": "...",
    "projects": "...",
    "reports": "...",
    "cache": "..."
  }
}

get_runtime_settings()

OrchestratorService.get_runtime_settings() now includes:

json
{
  "runtime": {
    "runtime_dir": "..."
  },
  "runtime_paths": {
    "root": "...",
    "outputs": "...",
    "uploads": "...",
    "projects": "...",
    "reports": "...",
    "cache": "..."
  }
}


The metadata reports effective storage paths. Therefore if outputs_path,
uploads_path, or projects_path are explicitly overridden, the metadata shows
those effective paths.

API Visibility

Because the API already exposes:

text
GET /health
GET /settings/runtime


runtime path metadata is now visible through these existing endpoints without
adding new endpoints.

Tests Added

Runtime path foundation tests:

text
tests/test_runtime_paths.py


These cover:

text
- default root layout
- environment variable override
- explicit root override
- no directory creation before ensure()
- directory creation with ensure()
- JSON-safe as_dict() output


Orchestrator integration tests:

text
tests/test_orchestrator_runtime_paths.py


These cover:

text
- OrchestratorService uses SMART_SPATIAL_RUNTIME_DIR by default
- explicit storage paths override runtime environment
- explicit runtime_dir sets default storage paths
- service initialization does not create runtime directories
- get_health exposes effective runtime paths
- get_runtime_settings exposes effective runtime paths

.gitignore Update

The repository ignore rules now include runtime/generated state patterns such as:

awk
/var/
/outputs/
/output/
/uploads/
/projects/
/cache/
/artifacts/reports/


It also includes backup/temp and frontend generated paths such as:

jboss-cli
*.bak
*.bak.*
*.before-*.bak
*.old
*backup*
/frontend/node_modules/
/frontend/dist/


Important:

text
Adding ignore rules does not remove already tracked files.


Tracked noisy files must be reviewed separately and removed from git only if
appropriate.

Backward Compatibility

This step intentionally preserved backward compatibility.

Existing tests and callers that pass explicit paths like:

python
OrchestratorServiceConfig(
    outputs_path=tmp_path / "outputs",
    uploads_path=tmp_path / "uploads",
    projects_path=tmp_path / "projects",
)


continue to work exactly as before.

Existing storage-level tests that instantiate:

python
OutputStorageConfig(root_dir=...)
UploadStorageConfig(root_dir=...)
ProjectStoreConfig(root_dir=...)


also continue to work.

What Changed

Changed:

text
- canonical runtime path abstraction added
- default OrchestratorService storage paths now resolve through RuntimePaths
- SMART_SPATIAL_RUNTIME_DIR can control default storage locations
- runtime paths are exposed in health/runtime metadata
- .gitignore includes runtime/generated paths

What Did Not Change

This step did not:

text
- delete existing outputs/uploads/projects directories
- move existing runtime files
- migrate old generated reports
- remove tracked files from git
- change API contracts
- change storage file formats
- change request/output manifest schemas
- refactor ProjectService/UploadService/OutputService yet
- extract microservices

Why Existing Runtime Directories Were Not Deleted

Directories such as:

text
outputs/
uploads/
projects/
artifacts/reports/
cache/
output/


may contain:

text
- local manual run data
- generated outputs useful for inspection
- test/debug artifacts
- previously tracked files


Deleting or moving them blindly could cause confusion or data loss.

Cleanup must happen separately and incrementally.

The safe process is:

text
1. inspect tracked noisy files
2. decide category by category
3. remove from git with git rm --cached if needed
4. keep local copies if valuable
5. run tests
6. commit small cleanup changes

Remaining Follow-up Work

The following work remains for later Phase 6 steps:

Repository cleanup

Review noisy tracked files:

bash
git ls-files | grep -E '(\.bak|\.before-|^outputs/|^uploads/|^projects/|^cache/|^output/|^artifacts/reports/|^frontend/node_modules/|^frontend/dist/)'


Then decide whether to:

text
- keep
- archive
- remove from git but keep locally
- delete if generated and reproducible

Report path alignment

Generated report outputs should eventually use:

text
runtime_paths.reports


This should happen when report/document generation is extracted or cleaned.

Cache path alignment

Cache users should eventually use:

text
runtime_paths.cache


This should be done when cache/plugin runtime behavior is reviewed.

Service extraction

Runtime paths will support later extraction of internal services:

text
ProjectService
UploadService
Artifact/OutputService
MapLayerService
Report/DocumentService
DataSourceService

Operational Usage

Default local runtime behavior:

bash
PYTHONPATH=. uvicorn api.main:app --reload


Uses:

text
var/
  outputs/
  uploads/
  projects/
  reports/
  cache/


Custom runtime root:

bash
SMART_SPATIAL_RUNTIME_DIR=/tmp/smart-spatial-runtime \
PYTHONPATH=. uvicorn api.main:app --reload


Uses:

text
/tmp/smart-spatial-runtime/outputs
/tmp/smart-spatial-runtime/uploads
/tmp/smart-spatial-runtime/projects
/tmp/smart-spatial-runtime/reports
/tmp/smart-spatial-runtime/cache

Definition of Done
text
[x] RuntimePaths abstraction added
[x] Runtime path tests added
[x] OrchestratorService default storage paths wired to RuntimePaths
[x] Explicit storage path override preserved
[x] SMART_SPATIAL_RUNTIME_DIR supported
[x] Health metadata exposes runtime paths
[x] Runtime settings metadata exposes runtime paths
[x] .gitignore updated for generated/runtime state
[x] Existing runtime directories not destructively modified
[x] Tests green
[x] Changes committed

Conclusion

Phase 6 / Step 2 is complete.

The backend now has a clear runtime directory standard and a safe path
resolution mechanism. Generated local state has a canonical home under var/,
while existing explicit path behavior remains supported.

This prepares the project for the next Phase 6 objective:

text
Phase 6 / Step 3 — Extract ProjectService
