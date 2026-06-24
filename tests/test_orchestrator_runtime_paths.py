from pathlib import Path

from orchestrator.runtime_paths import ENV_RUNTIME_DIR
from orchestrator.service import OrchestratorService, OrchestratorServiceConfig


def test_orchestrator_service_uses_runtime_env_for_default_storage_paths(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv(ENV_RUNTIME_DIR, str(runtime_root))

    service = OrchestratorService(OrchestratorServiceConfig())

    assert service.runtime_paths.root == runtime_root
    assert service.output_storage.root_dir == runtime_root / "outputs"
    assert service.upload_storage.root_dir == runtime_root / "uploads"
    assert service.project_store.root_dir == runtime_root / "projects"


def test_orchestrator_service_explicit_storage_paths_override_runtime_env(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv(ENV_RUNTIME_DIR, str(runtime_root))

    outputs_path = tmp_path / "custom-outputs"
    uploads_path = tmp_path / "custom-uploads"
    projects_path = tmp_path / "custom-projects"

    service = OrchestratorService(
        OrchestratorServiceConfig(
            outputs_path=outputs_path,
            uploads_path=uploads_path,
            projects_path=projects_path,
        )
    )

    assert service.runtime_paths.root == runtime_root
    assert service.output_storage.root_dir == outputs_path
    assert service.upload_storage.root_dir == uploads_path
    assert service.project_store.root_dir == projects_path


def test_orchestrator_service_explicit_runtime_dir_sets_default_storage_paths(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(ENV_RUNTIME_DIR, raising=False)

    runtime_root = tmp_path / "explicit-runtime"

    service = OrchestratorService(
        OrchestratorServiceConfig(
            runtime_dir=runtime_root,
        )
    )

    assert service.runtime_paths.root == runtime_root
    assert service.output_storage.root_dir == runtime_root / "outputs"
    assert service.upload_storage.root_dir == runtime_root / "uploads"
    assert service.project_store.root_dir == runtime_root / "projects"


def test_orchestrator_service_does_not_create_runtime_dirs_on_init(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv(ENV_RUNTIME_DIR, str(runtime_root))

    service = OrchestratorService(OrchestratorServiceConfig())

    assert service.runtime_paths.root == runtime_root
    assert not runtime_root.exists()
    assert not Path(service.output_storage.root_dir).exists()
    assert not Path(service.upload_storage.root_dir).exists()
    assert not Path(service.project_store.root_dir).exists()
