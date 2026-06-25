from __future__ import annotations

from typing import Any, Callable


class RequestHistoryService:
    def __init__(
        self,
        *,
        history: dict[str, dict[str, Any]],
        config_getter: Callable[[], Any],
        project_service_getter: Callable[[], Any],
    ) -> None:
        self._history = history
        self._config_getter = config_getter
        self._project_service_getter = project_service_getter

    def get_request(
        self,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        Return stored request record.
        """
        return self._history.get(request_id)

    def list_requests(self) -> list[dict[str, Any]]:
        """
        Return lightweight request history.
        """
        items: list[dict[str, Any]] = []

        for request_id, record in self._history.items():
            response = record.get("production_response") or {}

            items.append(
                {
                    "request_id": request_id,
                    "status": response.get("status"),
                    "answer": response.get("answer"),
                    "query": record.get("query"),
                }
            )

        return items

    def remember(
        self,
        *,
        request_id: str,
        record: dict[str, Any],
    ) -> None:
        config = self._config_getter()

        if not getattr(config, "keep_history", True):
            return

        max_history_items = getattr(config, "max_history_items", 1000)

        if max_history_items == 0:
            return

        self._history[request_id] = record

        # Link this request to its project so the UI history stays persistent.
        project_id = str(record.get("project_id") or "").strip()
        if project_id:
            try:
                project_service = self._project_service_getter()
                if project_service is not None:
                    project_service.attach_request(project_id, request_id)
            except Exception:
                pass

        if len(self._history) > max_history_items:
            overflow = len(self._history) - max_history_items

            for key in list(self._history.keys())[:overflow]:
                self._history.pop(key, None)

    def size(self) -> int:
        return len(self._history)
