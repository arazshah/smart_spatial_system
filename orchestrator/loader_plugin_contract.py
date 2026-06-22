"""
orchestrator.loader_plugin_contract

Formal contract for loader plugins.

Required canonical plugin functions:

Raster:
    load_local_raster(path: str, options: dict | None = None) -> dict

Vector:
    load_local_vector(path: str, options: dict | None = None) -> dict

The module validates and normalizes plugin outputs before they enter the
orchestration pipeline.

For transition compatibility, the caller supports:
    - load_local_raster(path=..., options=...)
    - load_local_raster(path=...)
    - load_local_raster(str_path)
    - load_local_vector(path=..., options=...)
    - load_local_vector(path=...)
    - load_local_vector(str_path)

But the canonical production contract remains:
    load_local_raster(path: str, options: dict | None = None) -> dict
    load_local_vector(path: str, options: dict | None = None) -> dict
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from orchestrator.input_error_mapping import loader_exception_to_structured_error


LOADER_PLUGIN_CONTRACT_VERSION = "1.0.0"


class LoaderPluginContractError(RuntimeError):
    """
    Raised when a loader plugin does not satisfy the loader contract.

    The legacy message remains unchanged; structured_error is additive.
    """

    def __init__(
        self,
        message: str,
        *,
        structured_error: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.structured_error = structured_error


def _loader_error(
    message: str,
    *,
    cause: BaseException | None = None,
    module_name: str | None = None,
    kind: str | None = None,
    function_name: str | None = None,
    stage: str | None = None,
    details: dict[str, Any] | None = None,
) -> LoaderPluginContractError:
    exc = cause if cause is not None else RuntimeError(message)
    return LoaderPluginContractError(
        message,
        structured_error=loader_exception_to_structured_error(
            exc,
            module_name=module_name,
            kind=kind,
            function_name=function_name,
            stage=stage,
            message=message,
            details=details,
        ),
    )


def load_with_loader_contract(
    *,
    module_name: str,
    kind: str,
    file_path: str | Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Load raster/vector through the canonical loader plugin contract.

    kind:
        "raster" or "vector"
    """
    if kind not in {"raster", "vector"}:
        raise _loader_error(
            f"Unsupported loader kind: {kind}",
            kind=kind,
            stage="validate_arguments",
        )

    if not module_name:
        raise _loader_error(
            "module_name must not be empty.",
            kind=kind,
            stage="validate_arguments",
        )

    path_text = str(file_path)
    final_options = dict(options or {})

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise _loader_error(
            f"Could not import loader plugin '{module_name}': {exc}",
            cause=exc,
            module_name=module_name,
            kind=kind,
            stage="plugin_import",
        ) from exc

    function_name = (
        "load_local_raster"
        if kind == "raster"
        else "load_local_vector"
    )

    loader = getattr(module, function_name, None)

    if not callable(loader):
        raise _loader_error(
            f"Loader plugin '{module_name}' must define callable "
            f"{function_name}(path: str, options: dict | None = None).",
            module_name=module_name,
            kind=kind,
            function_name=function_name,
            stage="contract_validation",
        )

    raw_result = _call_loader(
        loader,
        path_text=path_text,
        options=final_options,
        function_name=function_name,
        module_name=module_name,
        kind=kind,
    )

    plain_result = _to_plain(raw_result)

    if kind == "raster":
        return normalize_raster_loader_output(
            plain_result,
            source_module=module_name,
            source_path=path_text,
        )

    return normalize_vector_loader_output(
        plain_result,
        source_module=module_name,
        source_path=path_text,
    )


def normalize_raster_loader_output(
    value: Any,
    *,
    source_module: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    """
    Normalize and validate raster loader output.

    Accepted canonical shape:
        {
            "data": [...],
            "metadata": {...}
        }

    Also accepted wrappers:
        {"raster": {...}}
        {"payload": {...}}
        {"output": {...}}
    """
    payload = _unwrap_payload(value, preferred_keys=["raster", "payload", "output"])
    payload = _to_plain(payload)

    if not isinstance(payload, dict):
        raise _loader_error(
            "Raster loader output must be a dict-like object.",
            module_name=source_module,
            kind="raster",
            stage="normalize_output",
            details={"source_path": source_path},
        )

    data = payload.get("data")

    if not isinstance(data, list):
        raise _loader_error(
            "Raster loader output must contain 'data' as a list.",
            module_name=source_module,
            kind="raster",
            stage="normalize_output",
            details={"source_path": source_path},
        )

    metadata = payload.get("metadata")

    if metadata is None:
        metadata = {}

    if not isinstance(metadata, dict):
        raise _loader_error(
            "Raster loader output 'metadata' must be an object.",
            module_name=source_module,
            kind="raster",
            stage="normalize_output",
            details={"source_path": source_path},
        )

    normalized = dict(payload)
    normalized["data"] = data
    normalized["metadata"] = dict(metadata)

    normalized["metadata"].setdefault(
        "loader_contract_version",
        LOADER_PLUGIN_CONTRACT_VERSION,
    )

    if source_module:
        normalized["metadata"].setdefault(
            "loader_plugin_module",
            source_module,
        )

    if source_path:
        normalized["metadata"].setdefault(
            "source_path",
            source_path,
        )

    return normalized


def normalize_vector_loader_output(
    value: Any,
    *,
    source_module: str | None = None,
    source_path: str | None = None,
) -> dict[str, Any]:
    """
    Normalize and validate vector loader output.

    Accepted canonical shape:
        GeoJSON FeatureCollection:
        {
            "type": "FeatureCollection",
            "features": [...]
        }

    Also accepted wrappers:
        {"vector": {...}}
        {"geojson": {...}}
        {"payload": {...}}
        {"output": {...}}

    Also accepted:
        {"features": [...]}
    which is normalized to a FeatureCollection.
    """
    payload = _unwrap_payload(
        value,
        preferred_keys=["vector", "geojson", "payload", "output"],
    )
    payload = _to_plain(payload)

    if not isinstance(payload, dict):
        raise _loader_error(
            "Vector loader output must be a dict-like object.",
            module_name=source_module,
            kind="vector",
            stage="normalize_output",
            details={"source_path": source_path},
        )

    if payload.get("type") == "FeatureCollection":
        features = payload.get("features")

        if not isinstance(features, list):
            raise _loader_error(
                "Vector FeatureCollection must contain 'features' as a list.",
                module_name=source_module,
                kind="vector",
                stage="normalize_output",
                details={"source_path": source_path},
            )

        normalized = dict(payload)

    elif isinstance(payload.get("features"), list):
        normalized = {
            "type": "FeatureCollection",
            "features": payload["features"],
        }

        if isinstance(payload.get("metadata"), dict):
            normalized["metadata"] = dict(payload["metadata"])

    else:
        raise _loader_error(
            "Vector loader output must be a GeoJSON FeatureCollection "
            "or contain 'features' as a list.",
            module_name=source_module,
            kind="vector",
            stage="normalize_output",
            details={"source_path": source_path},
        )

    metadata = normalized.get("metadata")

    if metadata is None:
        metadata = {}

    if not isinstance(metadata, dict):
        metadata = {
            "value": metadata,
        }

    metadata.setdefault(
        "loader_contract_version",
        LOADER_PLUGIN_CONTRACT_VERSION,
    )

    if source_module:
        metadata.setdefault(
            "loader_plugin_module",
            source_module,
        )

    if source_path:
        metadata.setdefault(
            "source_path",
            source_path,
        )

    normalized["metadata"] = metadata

    return normalized


def _call_loader(
    loader: Any,
    *,
    path_text: str,
    options: dict[str, Any],
    function_name: str,
    module_name: str,
    kind: str,
) -> Any:
    """
    Transitional callable invocation.

    Production contract:
        loader(path: str, options: dict | None = None)

    Compatibility:
        loader(path=..., options=...)
        loader(path=...)
        loader(str_path, options)
        loader(str_path)
    """
    attempts = [
        ((), {"path": path_text, "options": options}),
        ((path_text, options), {}),
        ((), {"path": path_text}),
        ((path_text,), {}),
    ]

    errors: list[str] = []

    for args, kwargs in attempts:
        try:
            return loader(*args, **kwargs)
        except TypeError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:
            raise _loader_error(
                f"Loader '{module_name}.{function_name}' failed: {exc}",
                cause=exc,
                module_name=module_name,
                kind=kind,
                function_name=function_name,
                stage="loader_execution",
            ) from exc

    raise _loader_error(
        f"Loader '{module_name}.{function_name}' could not be called with "
        f"the standard contract. Errors: {' | '.join(errors[-4:])}",
        module_name=module_name,
        kind=kind,
        function_name=function_name,
        stage="loader_call_contract",
        details={"attempt_errors": errors[-4:]},
    )


def _unwrap_payload(
    value: Any,
    *,
    preferred_keys: list[str],
) -> Any:
    payload = _to_plain(value)

    if not isinstance(payload, dict):
        return payload

    for key in preferred_keys:
        inner = payload.get(key)

        if isinstance(inner, dict):
            return inner

    return payload


def _to_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): _to_plain(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _to_plain(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            _to_plain(item)
            for item in value
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _to_plain(value.to_dict())
        except Exception:
            pass

    if is_dataclass(value):
        try:
            return _to_plain(asdict(value))
        except Exception:
            pass

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return _to_plain(payload)

    return value
