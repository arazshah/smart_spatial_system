from pathlib import Path

from orchestrator.runtime_paths import (
    DEFAULT_RUNTIME_DIR,
    ENV_RUNTIME_DIR,
    RuntimePaths,
)


def test_runtime_paths_default_root_has_expected_layout(monkeypatch):
    monkeypatch.delenv(ENV_RUNTIME_DIR, raising=False)

    paths = RuntimePaths.from_env()

    assert paths.root == Path(DEFAULT_RUNTIME_DIR)
    assert paths.outputs == Path(DEFAULT_RUNTIME_DIR) / "outputs"
    assert paths.uploads == Path(DEFAULT_RUNTIME_DIR) / "uploads"
    assert paths.projects == Path(DEFAULT_RUNTIME_DIR) / "projects"
    assert paths.reports == Path(DEFAULT_RUNTIME_DIR) / "reports"
    assert paths.cache == Path(DEFAULT_RUNTIME_DIR) / "cache"


def test_runtime_paths_env_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime-root"
    monkeypatch.setenv(ENV_RUNTIME_DIR, str(runtime_root))

    paths = RuntimePaths.from_env()

    assert paths.root == runtime_root
    assert paths.outputs == runtime_root / "outputs"
    assert paths.uploads == runtime_root / "uploads"
    assert paths.projects == runtime_root / "projects"
    assert paths.reports == runtime_root / "reports"
    assert paths.cache == runtime_root / "cache"


def test_runtime_paths_explicit_root_wins_over_env(monkeypatch, tmp_path):
    env_root = tmp_path / "env-root"
    explicit_root = tmp_path / "explicit-root"
    monkeypatch.setenv(ENV_RUNTIME_DIR, str(env_root))

    paths = RuntimePaths.from_env(explicit_root)

    assert paths.root == explicit_root
    assert paths.outputs == explicit_root / "outputs"


def test_runtime_paths_does_not_create_directories_before_ensure(tmp_path):
    runtime_root = tmp_path / "runtime-root"

    paths = RuntimePaths.from_root(runtime_root)

    assert not runtime_root.exists()
    assert not paths.outputs.exists()
    assert not paths.uploads.exists()
    assert not paths.projects.exists()
    assert not paths.reports.exists()
    assert not paths.cache.exists()


def test_runtime_paths_ensure_creates_expected_directories(tmp_path):
    runtime_root = tmp_path / "runtime-root"

    paths = RuntimePaths.from_root(runtime_root).ensure()

    assert paths.root.is_dir()
    assert paths.outputs.is_dir()
    assert paths.uploads.is_dir()
    assert paths.projects.is_dir()
    assert paths.reports.is_dir()
    assert paths.cache.is_dir()


def test_runtime_paths_as_dict_returns_string_paths(tmp_path):
    runtime_root = tmp_path / "runtime-root"

    paths = RuntimePaths.from_root(runtime_root)

    assert paths.as_dict() == {
        "root": str(runtime_root),
        "outputs": str(runtime_root / "outputs"),
        "uploads": str(runtime_root / "uploads"),
        "projects": str(runtime_root / "projects"),
        "reports": str(runtime_root / "reports"),
        "cache": str(runtime_root / "cache"),
    }
