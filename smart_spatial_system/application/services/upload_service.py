from __future__ import annotations

from typing import Any

from orchestrator.upload_storage import UploadStorage, UploadStorageError


class UploadServiceError(RuntimeError):
    """Raised when an upload service operation fails."""


class UploadService:
    """Application service boundary for upload operations."""

    def __init__(self, upload_storage: UploadStorage | None) -> None:
        if upload_storage is None:
            raise UploadServiceError("UploadStorage dependency is required.")
        self._upload_storage = upload_storage

    @property
    def upload_storage(self) -> UploadStorage:
        return self._upload_storage

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            method = getattr(self._upload_storage, method_name)
            return method(*args, **kwargs)
        except UploadStorageError as exc:
            raise UploadServiceError(str(exc)) from exc

    def save_upload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("save_upload", *args, **kwargs)

    def list_uploads(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("list_uploads", *args, **kwargs)

    def read_metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("read_metadata", *args, **kwargs)

    def get_file_path(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_file_path", *args, **kwargs)

    def get_media_type(self, *args: Any, **kwargs: Any) -> str:
        return self._call("get_media_type", *args, **kwargs)

    def save_external_source(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("save_external_source", *args, **kwargs)

    def delete_upload(self, *args: Any, **kwargs: Any) -> None:
        return self._call("delete_upload", *args, **kwargs)

    def update_metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("update_metadata", *args, **kwargs)

    def read_json_content(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("read_json_content", *args, **kwargs)
