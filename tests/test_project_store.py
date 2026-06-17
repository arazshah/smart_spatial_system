"""
Tests for ProjectStore.

Run:
    pytest tests/test_project_store.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.project_store import (  # noqa: E402
    PROJECT_STORE_SCHEMA_VERSION,
    ProjectStore,
    ProjectStoreConfig,
    ProjectStoreError,
)


def test_project_store_create_and_get_project(tmp_path: Path) -> None:
    store = ProjectStore(
        ProjectStoreConfig(
            root_dir=tmp_path / "projects",
        )
    )

    project = store.create_project(
        name="Vegetation Analysis",
        description="NDVI workflow",
        metadata={"owner": "test"},
    )

    assert project["schema_version"] == PROJECT_STORE_SCHEMA_VERSION
    assert project["project_id"].startswith("prj-")
    assert project["name"] == "Vegetation Analysis"

    loaded = store.get_project(project["project_id"])

    assert loaded["project_id"] == project["project_id"]
    assert loaded["metadata"]["owner"] == "test"


def test_project_store_list_projects(tmp_path: Path) -> None:
    store = ProjectStore(
        ProjectStoreConfig(
            root_dir=tmp_path / "projects",
        )
    )

    store.create_project(name="A")
    store.create_project(name="B")

    items = store.list_projects()

    assert len(items) == 2


def test_project_store_attach_items(tmp_path: Path) -> None:
    store = ProjectStore(
        ProjectStoreConfig(
            root_dir=tmp_path / "projects",
        )
    )

    project = store.create_project(name="My Project")
    project_id = project["project_id"]

    project = store.attach_upload(project_id, "upl-1")
    project = store.attach_request(project_id, "req-1")
    project = store.attach_output(project_id, "req-1")
    project = store.attach_feedback(project_id, "fb-1")

    assert project["uploads"] == ["upl-1"]
    assert project["requests"] == ["req-1"]
    assert project["outputs"] == ["req-1"]
    assert project["feedback"] == ["fb-1"]


def test_project_store_rejects_invalid_name(tmp_path: Path) -> None:
    store = ProjectStore(
        ProjectStoreConfig(
            root_dir=tmp_path / "projects",
        )
    )

    with pytest.raises(ProjectStoreError, match="name"):
        store.create_project(name="  ")


def test_project_store_config_rejects_invalid_indent() -> None:
    with pytest.raises(ValueError, match="indent"):
        ProjectStoreConfig(indent=-1)
