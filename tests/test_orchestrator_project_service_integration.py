from pathlib import Path

import pytest

from orchestrator.project_service import ProjectService
from orchestrator.service import (
    DEFAULT_SAFE_PLUGIN_MODULES,
    OrchestratorService,
    OrchestratorServiceConfig,
    OrchestratorServiceError,
)


def _service(tmp_path: Path) -> OrchestratorService:
    return OrchestratorService(
        OrchestratorServiceConfig(
            plugin_modules=list(DEFAULT_SAFE_PLUGIN_MODULES),
            weights_path=tmp_path / "weights" / "router_weights.json",
            outputs_path=tmp_path / "outputs",
            uploads_path=tmp_path / "uploads",
            projects_path=tmp_path / "projects",
        )
    )


def test_orchestrator_service_wires_project_service(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert isinstance(service.project_service, ProjectService)
    assert service.project_service.store is service.project_store


def test_orchestrator_service_delegates_basic_project_methods(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    project = service.create_project(
        name="Delegated Project",
        description="Created through OrchestratorService",
        metadata={"owner": "tester"},
    )

    project_id = project["project_id"]

    assert project_id.startswith("prj-")
    assert project["name"] == "Delegated Project"

    items = service.list_projects()

    assert len(items) == 1
    assert items[0]["project_id"] == project_id

    loaded = service.get_project(project_id)

    assert loaded["project_id"] == project_id
    assert loaded["metadata"]["owner"] == "tester"


def test_orchestrator_service_converts_project_service_errors(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(OrchestratorServiceError):
        service.get_project("missing-project")
