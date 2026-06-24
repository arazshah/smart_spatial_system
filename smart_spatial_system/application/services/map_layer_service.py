from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MapLayerServiceError(RuntimeError):
    """Raised when a map layer service operation fails."""


class MapLayerService:
    """Application service boundary for map layer operations."""

    def __init__(
        self,
        get_request: Callable[[str], dict[str, Any]] | None,
        map_layer_builder: Any,
    ) -> None:
        if get_request is None:
            raise MapLayerServiceError("get_request dependency is required.")
        if map_layer_builder is None:
            raise MapLayerServiceError("map_layer_builder dependency is required.")
        self._get_request = get_request
        self.map_layer_builder = map_layer_builder

    def get_map_layers(
            self,
            request_id: str,
        ) -> dict[str, Any]:
            """
            Return Leaflet-ready map layers for a stored request.
            """
            record = self._get_request(request_id)

            if record is None:
                raise MapLayerServiceError(
                    f"Unknown request_id: {request_id}"
                )

            return self.map_layer_builder.build_for_request_record(record)
