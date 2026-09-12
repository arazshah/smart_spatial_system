"""
Tests for smart_spatial_system.cli (REFACTOR_PLAN Phase 8,
docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md step 4).

These are smoke/argument-parsing tests only: uvicorn.run is monkeypatched
so nothing actually starts a server or binds a port.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_spatial_system.cli import build_parser, main, serve


def test_build_parser_serve_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve"])

    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False
    assert args.log_level == "info"


def test_build_parser_serve_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["serve", "--host", "0.0.0.0", "--port", "9000", "--reload", "--log-level", "debug"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.reload is True
    assert args.log_level == "debug"


def test_serve_calls_uvicorn_run_with_api_main_app(monkeypatch) -> None:
    calls = []

    class _FakeUvicorn:
        @staticmethod
        def run(app_path, **kwargs):
            calls.append((app_path, kwargs))

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)

    serve(host="0.0.0.0", port=9000, reload=True, log_level="debug")

    assert len(calls) == 1
    app_path, kwargs = calls[0]
    assert app_path == "api.main:app"
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 9000,
        "reload": True,
        "log_level": "debug",
    }


def test_main_dispatches_serve_command(monkeypatch) -> None:
    calls = []

    def _fake_serve(*, host, port, reload, log_level):
        calls.append({"host": host, "port": port, "reload": reload, "log_level": log_level})

    monkeypatch.setattr("smart_spatial_system.cli.serve", _fake_serve)

    main(["serve", "--host", "127.0.0.1", "--port", "8080"])

    assert calls == [
        {"host": "127.0.0.1", "port": 8080, "reload": False, "log_level": "info"}
    ]


def test_main_requires_a_command() -> None:
    import pytest

    with pytest.raises(SystemExit):
        main([])
