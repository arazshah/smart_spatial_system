"""
config

Not application code — this package exists only so `config/plugins/*.yaml`
(the default plugin configuration shipped with the repo) can be bundled as
package data and located via `importlib.resources` after a `pip install`,
as a fallback when no `config/plugins` directory is reachable by walking up
from the current working directory (REFACTOR_PLAN Phase 8,
docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md step 2). See
`orchestrator/plugin_config_store.py::find_config_dir` and
`plugins/_shared/plugin_config.py::find_config_dir` for the resolution
order this fallback participates in.
"""
