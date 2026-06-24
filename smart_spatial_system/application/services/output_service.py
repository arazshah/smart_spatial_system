from __future__ import annotations

from typing import Any

from orchestrator.output_storage import OutputStorage, OutputStorageError


class OutputServiceError(RuntimeError):
    """Raised when an output service operation fails."""


class OutputService:
    """Application service boundary for request output operations."""

    def __init__(self, output_storage: OutputStorage | None) -> None:
        if output_storage is None:
            raise OutputServiceError("OutputStorage dependency is required.")
        self._output_storage = output_storage

    @property
    def output_storage(self) -> OutputStorage:
        return self._output_storage

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            method = getattr(self._output_storage, method_name)
            return method(*args, **kwargs)
        except OutputStorageError as exc:
            raise OutputServiceError(str(exc)) from exc

    def read_manifest(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("read_manifest", *args, **kwargs)

    def list_files(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("list_files", *args, **kwargs)

    def get_file_path(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("get_file_path", *args, **kwargs)

    def get_media_type(self, *args: Any, **kwargs: Any) -> str:
        return self._call("get_media_type", *args, **kwargs)

    def save_request_record(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call("save_request_record", *args, **kwargs)
