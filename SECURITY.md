# Security Policy

## Supported versions

Only the latest published release on [PyPI](https://pypi.org/project/smart-spatial-system/) and the `main` branch receive security fixes. There is no long-term-support branch at this stage — upgrade to the latest release to pick up a fix.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** Instead, report it privately via [GitHub's private vulnerability reporting](https://github.com/arazshah/smart_spatial_system/security/advisories/new) on this repository, or contact the maintainer directly through [araz.me](https://araz.me).

Include what you'd include in any bug report: the affected version, a reproduction, and the impact as you understand it. You should get an acknowledgement within a few days; a fix timeline depends on severity, and you'll be credited in the advisory unless you ask not to be.

## Known scope and deployment guidance

This project's threat model is documented, not assumed — read before deploying beyond your own machine:

- **Authentication is minimal by design.** `api/auth.py` is a shared-secret `X-API-Key` check sized for "one deployer, one team, self-hosted", not multi-tenant. `SMART_SPATIAL_API_KEY` unset means the API is fully open (a startup warning fires, it doesn't refuse to start) — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- **An LLM API key configured alongside an open API** lets anyone who reaches the instance spend that credit — the startup warning calls this out specifically.
- **PostGIS credentials** are read from environment variables (`config/plugins/postgis_connector.yaml`'s `password_env`), never hardcoded — never commit a `.env` file or a filled-in `config/plugins/*.yaml` with real credentials.
- **File uploads and local data loading** (`local_raster_loader`, `local_vector_loader`) are scoped to configured allowed roots — see each plugin's config for `allowed_roots`/equivalent before pointing it at a shared filesystem.

If you find a gap in this list itself — a place the code doesn't match this description — that's a security report too.
