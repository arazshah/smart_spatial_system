"""
orchestrator.project_service

Thin project domain service for the Smart Spatial System.

This service wraps ProjectStore and provides a stable service boundary for
project/session operations.

Phase 6 goal:
    - Keep ProjectStore as the file-based persistence layer.
    - Introduce ProjectService as the project/domain operation boundary.
    - Allow OrchestratorService to delegate project operations in a later step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.project_store import ProjectStore, ProjectStoreError


class ProjectServiceError(RuntimeError):
    """Project service level error."""


@dataclass(frozen=True)
class ProjectServiceConfig:
    """Configuration placeholder for future project service behavior."""

    enabled: bool = True


class ProjectService:
    """
    Thin service wrapper around ProjectStore.

    This class intentionally contains minimal logic for now. It creates a clear
    boundary so OrchestratorService can stop depending directly on ProjectStore
    for project-level operations in later Phase 6 steps.
    """

    def __init__(
        self,
        store: ProjectStore,
        config: ProjectServiceConfig | None = None,
    ) -> None:
        if store is None:
            raise ProjectServiceError("ProjectStore is required.")

        self.store = store
        self.config = config or ProjectServiceConfig()

    def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.store.create_project(
                name=name,
                description=description,
                metadata=metadata,
            )
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def list_projects(self) -> list[dict[str, Any]]:
        return self.store.list_projects()

    def get_project(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.get_project(project_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def attach_upload(
        self,
        project_id: str,
        upload_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.attach_upload(project_id, upload_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def detach_upload(
        self,
        project_id: str,
        upload_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.detach_upload(project_id, upload_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def attach_request(
        self,
        project_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.attach_request(project_id, request_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def attach_output(
        self,
        project_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.attach_output(project_id, request_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def attach_feedback(
        self,
        project_id: str,
        feedback_id: str,
    ) -> dict[str, Any]:
        try:
            return self.store.attach_feedback(project_id, feedback_id)
        except ProjectStoreError as exc:
            raise ProjectServiceError(str(exc)) from exc

    def find_project_id_for_upload(
        self,
        upload_id: str,
    ) -> str | None:
        """
        Return the first project_id that contains the upload_id.

        This preserves the current simple file-store behavior where uploads are
        attached to projects through project.json.
        """
        for project in self.list_projects():
            project_id = project.get("project_id")
            uploads = project.get("uploads")

            if project_id and isinstance(uploads, list) and upload_id in uploads:
                return str(project_id)

        return None

    def find_project_ids_for_upload(
        self,
        upload_id: str,
    ) -> list[str]:
        """
        Return all project_ids that contain the upload_id.
        """
        project_ids: list[str] = []

        for project in self.list_projects():
            project_id = project.get("project_id")
            uploads = project.get("uploads")

            if project_id and isinstance(uploads, list) and upload_id in uploads:
                project_ids.append(str(project_id))

        return project_ids
