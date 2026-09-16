# Contributing

Thanks for considering a contribution. This project follows [ADR-001](docs/ADR-001-single-kernel-pipeline.md): new functionality enters through the QuerySpec/DAG/plugin pipeline, not as a new direct handler — read it before proposing a feature that adds a special-case code path.

## Before you start

- Read `CLAUDE.md` for the layering (`smart_spatial_system/` is where real logic lives; `orchestrator/` is being migrated out of) and the current phase in `docs/SMART_SPATIAL_SYSTEM_BACKEND_ROADMAP.md`.
- For a new plugin, `docs/PLUGIN_FACTORY_AGENT_GUIDE.md` is the authoring guide — conventions, config, testing.
- For an architectural change, `docs/ARCHITECTURE_CURRENT.md` vs `docs/ARCHITECTURE_TARGET.md` tells you what's being migrated away from and toward, so a change doesn't fight the direction already decided.
- Search open issues and pull requests first — this avoids duplicate work on a project that moves fast.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
pip install -e ".[dev,raster,pdf,postgis]"
cp .env.example .env
pytest
ruff check .
```

`pytest` (currently 1700+ tests) and `ruff check .` both run in CI on every PR — a change isn't ready for review until both pass locally.

## Making a change

1. Branch from `main`.
2. Keep the change scoped to one thing — a bugfix and a refactor in the same PR are harder to review and to revert independently.
3. Add or update tests. A fix without a regression test that reproduces the original failure is treated as incomplete.
4. Update `CHANGELOG.md` under `[Unreleased]` (add that heading if it's not there) if the change is user-visible.
5. Run `pytest` and `ruff check .` before opening the PR.

## Pull requests

- Describe what changed and why, not just what — link the issue if there is one.
- Keep commits reasonably clean; squash-on-merge is fine, so history inside the branch doesn't need to be pristine.
- A maintainer will review; expect requested changes on a first PR to a codebase this size — that's normal, not a sign something went wrong.

## Reporting a bug

Open an issue with: the query or code that triggered it, the exact error/traceback, and your `smart-spatial-system` version (`pip show smart-spatial-system`). If you can reduce it to a minimal QuerySpec or a runnable snippet (see `examples/`), that turns a multi-day investigation into a five-minute fix.

## Security issues

Do not open a public issue for a security vulnerability — see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contribution is licensed under this project's [MIT license](LICENSE).
