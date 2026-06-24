from pathlib import Path

import pytest

from orchestrator.project_service import ProjectService, ProjectServiceError
from orchestrator.project_store import ProjectStore, ProjectStoreConfig


def _service(tmp_path: Path) -> ProjectService:
    store = ProjectStore(
        ProjectStoreConfig(
            root_dir=tmp_path / "projects",
        )
    )
    return ProjectService(store)


def test_project_service_create_list_and_get_project(tmp_path: Path) -> None:
    service = _service(tmp_path)

    project = service.create_project(
        name="Vegetation Project",
        description="NDVI workflow",
        metadata={"owner": "tester"},
    )

    assert project["project_id"].startswith("prj-")
    assert project["name"] == "Vegetation Project"

    items = service.list_projects()

    assert len(items) == 1
    assert items[0]["project_id"] == project["project_id"]

    loaded = service.get_project(project["project_id"])

    assert loaded["project_id"] == project["project_id"]
    assert loaded["metadata"]["owner"] == "tester"


def test_project_service_attach_and_detach_upload(tmp_path: Path) -> None:
    service = _service(tmp_path)

    project = service.create_project(name="Upload Project")
    project_id = project["project_id"]

    updated = service.attach_upload(project_id, "upl-1")

    assert updated["uploads"] == ["upl-1"]
    assert service.find_project_id_for_upload("upl-1") == project_id
    assert service.find_project_ids_for_upload("upl-1") == [project_id]

    updated = service.detach_upload(project_id, "upl-1")

    assert updated["uploads"] == []
    assert service.find_project_id_for_upload("upl-1") is None
    assert service.find_project_ids_for_upload("upl-1") == []


def test_project_service_attach_request_output_and_feedback(tmp_path: Path) -> None:
    service = _service(tmp_path)

    project = service.create_project(name="Request Project")
    project_id = project["project_id"]

    updated = service.attach_request(project_id, "req-1")
    updated = service.attach_output(project_id, "req-1")
    updated = service.attach_feedback(project_id, "fb-1")

    assert updated["requests"] == ["req-1"]
    assert updated["outputs"] == ["req-1"]
    assert updated["feedback"] == ["fb-1"]


def test_project_service_converts_store_errors(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ProjectServiceError):
        service.get_project("missing-project")


def test_project_service_requires_store() -> None:
    with pytest.raises(ProjectServiceError, match="ProjectStore is required"):
        ProjectService(None)  # type: ignore[arg-type]
