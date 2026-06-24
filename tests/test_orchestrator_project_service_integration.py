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


def test_orchestrator_service_does_not_use_project_store_for_attachments() -> None:
    from pathlib import Path

    service_source = Path("orchestrator/service.py").read_text(encoding="utf-8")
    data_source_source = Path(
        "smart_spatial_system/application/services/data_source_service.py"
    ).read_text(encoding="utf-8")

    forbidden_calls = [
        "self.project_store.attach_upload(",
        "self.project_store.detach_upload(",
    ]

    combined_source = service_source + "\n" + data_source_source

    for call in forbidden_calls:
        assert call not in combined_source

    assert "self.project_service.attach_upload(" in data_source_source
    assert "self.project_service.detach_upload(" in data_source_source



def test_orchestrator_service_does_not_use_project_store_for_project_lookups() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "self.project_store.create_project(" not in source
    assert "self.project_store.get_project(" not in source
    assert "self.project_store.list_projects(" not in source

    assert "self.project_service.create_project(" in source
    assert "self.project_service.get_project(" in source
    assert "self.project_service.list_projects(" in source


def test_orchestrator_service_no_longer_references_project_store_error() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "ProjectStoreError" not in source
    assert "ProjectServiceError" in source
