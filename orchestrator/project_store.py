"""
orchestrator.project_store

File-based project/session management for the Smart Spatial System.

Project structure:
    projects/
      {project_id}/
        project.json

This module manages:
    - create/get/list projects
    - attach uploads to project
    - attach requests to project
    - attach outputs to project
    - query project-related references

MVP approach:
    - project metadata is file-based JSON
    - relationships are stored inside project.json
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_STORE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class ProjectStoreConfig:
    root_dir: str | Path = "projects"
    indent: int = 2
    ensure_ascii: bool = False

    def __post_init__(self) -> None:
        if self.indent < 0:
            raise ValueError("indent must be >= 0.")


class ProjectStoreError(RuntimeError):
    pass


class ProjectStore:
    def __init__(
        self,
        config: ProjectStoreConfig | None = None,
    ) -> None:
        self.config = config or ProjectStoreConfig()
        self.root_dir = Path(self.config.root_dir)

    def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(name).strip():
            raise ProjectStoreError("Project name is required.")

        project_id = f"prj-{uuid.uuid4()}"
        directory = self.project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)

        project = {
            "schema_version": PROJECT_STORE_SCHEMA_VERSION,
            "project_id": project_id,
            "name": str(name).strip(),
            "description": str(description or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "metadata": _json_safe(metadata or {}),
            "uploads": [],
            "requests": [],
            "outputs": [],
            "feedback": [],
        }

        self._write_project(project_id, project)
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.root_dir.exists():
            return []

        items: list[dict[str, Any]] = []

        for child in sorted(self.root_dir.iterdir()):
            if not child.is_dir():
                continue

            project_file = child / "project.json"
            if not project_file.exists():
                continue

            try:
                items.append(self._read_json(project_file))
            except Exception:
                continue

        items.sort(
            key=lambda item: item.get("updated_at") or "",
            reverse=True,
        )
        return items

    def get_project(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        path = self.project_file(project_id)

        if not path.exists():
            raise ProjectStoreError(f"Unknown project_id: {project_id}")

        return self._read_json(path)

    def attach_upload(
        self,
        project_id: str,
        upload_id: str,
    ) -> dict[str, Any]:
        return self._attach_unique_item(
            project_id,
            key="uploads",
            item=upload_id,
        )


    def detach_upload(
        self,
        project_id: str,
        upload_id: str,
    ) -> dict[str, Any]:
        return self._detach_item(
            project_id,
            key="uploads",
            item=upload_id,
        )

    def attach_request(
        self,
        project_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        return self._attach_unique_item(
            project_id,
            key="requests",
            item=request_id,
        )

    def attach_output(
        self,
        project_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        return self._attach_unique_item(
            project_id,
            key="outputs",
            item=request_id,
        )

    def attach_feedback(
        self,
        project_id: str,
        feedback_id: str,
    ) -> dict[str, Any]:
        return self._attach_unique_item(
            project_id,
            key="feedback",
            item=feedback_id,
        )

    def project_dir(
        self,
        project_id: str,
    ) -> Path:
        return self.root_dir / _safe_project_id(project_id)

    def project_file(
        self,
        project_id: str,
    ) -> Path:
        return self.project_dir(project_id) / "project.json"

    def _attach_unique_item(
        self,
        project_id: str,
        *,
        key: str,
        item: str,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)

        values = project.get(key)
        if not isinstance(values, list):
            values = []

        if item not in values:
            values.append(item)

        project[key] = values
        project["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._write_project(project_id, project)
        return project


    def _detach_item(
        self,
        project_id: str,
        *,
        key: str,
        item: str,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)

        values = project.get(key)
        if not isinstance(values, list):
            values = []

        project[key] = [
            value for value in values
            if value != item
        ]
        project["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._write_project(project_id, project)
        return project

    def _write_project(
        self,
        project_id: str,
        project: dict[str, Any],
    ) -> None:
        directory = self.project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)

        path = directory / "project.json"

        try:
            path.write_text(
                json.dumps(
                    _json_safe(project),
                    ensure_ascii=self.config.ensure_ascii,
                    indent=self.config.indent,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ProjectStoreError(
                f"Failed to write project file {path}: {exc}"
            ) from exc

    @staticmethod
    def _read_json(
        path: Path,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProjectStoreError(
                f"Invalid project JSON {path}: {exc}"
            ) from exc
        except OSError as exc:
            raise ProjectStoreError(
                f"Failed to read project file {path}: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ProjectStoreError(f"Project file must be object: {path}")

        return payload


def _safe_project_id(value: str) -> str:
    text = str(value).strip()

    if not text.startswith("prj-"):
        raise ProjectStoreError("Invalid project_id.")

    if not re.fullmatch(r"prj-[A-Za-z0-9_.-]+", text):
        raise ProjectStoreError("Invalid project_id.")

    return text


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass

    if is_dataclass(value):
        try:
            return _json_safe(asdict(value))
        except Exception:
            pass

    payload = getattr(value, "__dict__", None)
    if isinstance(payload, dict) and payload:
        return _json_safe(payload)

    return repr(value)
