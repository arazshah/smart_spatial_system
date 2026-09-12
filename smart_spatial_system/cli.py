"""
smart_spatial_system.cli

Command-line entrypoint for the packaged backend (REFACTOR_PLAN Phase 8,
docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md). `serve` wraps the same
`api.main:app` factory used by `uvicorn api.main:app --reload` in local
dev and by the Docker image's CMD - this does not reimplement app
construction, it just gives that app a pip-installed entrypoint
(`smart-spatial-api serve ...` / `python -m smart_spatial_system serve
...`, per [project.scripts] in pyproject.toml and __main__.py).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smart-spatial-api",
        description="Smart Spatial System backend CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser(
        "serve", help="Run the FastAPI backend with uvicorn."
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (development only).",
    )
    serve_parser.add_argument("--log-level", default="info")

    return parser


def serve(*, host: str, port: int, reload: bool, log_level: str) -> None:
    import uvicorn

    uvicorn.run("api.main:app", host=host, port=port, reload=reload, log_level=log_level)


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
        return

    parser.error(f"Unknown command: {args.command}")  # pragma: no cover - argparse guards this


if __name__ == "__main__":
    main()
