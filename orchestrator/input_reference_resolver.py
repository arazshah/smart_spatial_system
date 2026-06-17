"""
orchestrator.input_reference_resolver

Plugin-based resolver for uploaded input references.

Main purpose:
    Convert:
        {"raster_ref": "upl-..."}
        {"vector_ref": "upl-..."}

    Into:
        {"raster": <loaded raster payload>}
        {"vector": <loaded vector payload>}

Operational rule:
    - Raster files should be loaded through local_raster_loader plugin.
    - Vector files should be loaded through local_vector_loader plugin.
    - JSON uploads can be used as fallback for MVP compatibility.

The resolver is intentionally adaptive because plugin implementations may expose
slightly different callable names.
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from orchestrator.upload_storage import UploadStorage, UploadStorageError
from orchestrator.loader_plugin_contract import (
    LoaderPluginContractError,
    load_with_loader_contract,
)


@dataclass(frozen=True)
class UploadReferenceResolverConfig:
    raster_loader_plugin_module: str = "plugins.local_raster_loader"
    vector_loader_plugin_module: str = "plugins.local_vector_loader"

    use_plugins: bool = True
    allow_json_fallback: bool = True

    prefer_plugin_for_json: bool = False
    enforce_loader_contract: bool = True
    allow_adaptive_loader_fallback: bool = True

    def __post_init__(self) -> None:
        if not self.raster_loader_plugin_module:
            raise ValueError("raster_loader_plugin_module must not be empty.")

        if not self.vector_loader_plugin_module:
            raise ValueError("vector_loader_plugin_module must not be empty.")


class UploadReferenceResolverError(RuntimeError):
    pass


class UploadReferenceResolver:
    """
    Resolve uploaded file references into pipeline-ready input payloads.
    """

    def __init__(
        self,
        upload_storage: UploadStorage,
        config: UploadReferenceResolverConfig | None = None,
    ) -> None:
        self.upload_storage = upload_storage
        self.config = config or UploadReferenceResolverConfig()

    def resolve_inputs(
        self,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(inputs, dict):
            raise UploadReferenceResolverError("inputs must be a dict.")

        resolved = dict(inputs)

        raster_ref = self._extract_ref(
            resolved,
            direct_key="raster_ref",
            object_key="raster",
        )

        if raster_ref:
            resolved.pop("raster_ref", None)
            resolved["raster"] = self.resolve_upload_ref(
                str(raster_ref),
                kind="raster",
            )

        vector_ref = self._extract_ref(
            resolved,
            direct_key="vector_ref",
            object_key="vector",
        )

        if vector_ref:
            resolved.pop("vector_ref", None)
            resolved["vector"] = self.resolve_upload_ref(
                str(vector_ref),
                kind="vector",
            )

        return resolved

    def resolve_upload_ref(
        self,
        upload_id: str,
        *,
        kind: str,
    ) -> Any:
        if kind not in {"raster", "vector"}:
            raise UploadReferenceResolverError(
                f"Unsupported reference kind: {kind}"
            )

        try:
            metadata = self.upload_storage.read_metadata(upload_id)
            file_path = self.upload_storage.get_file_path(upload_id)
        except UploadStorageError as exc:
            raise UploadReferenceResolverError(str(exc)) from exc

        extension = str(metadata.get("extension") or Path(file_path).suffix).lower()
        is_json_like = extension in {".json", ".geojson"}

        if self.config.use_plugins and (
            self.config.prefer_plugin_for_json or not is_json_like
        ):
            return self._load_with_plugin(
                kind=kind,
                file_path=file_path,
                metadata=metadata,
            )

        if is_json_like and self.config.allow_json_fallback:
            try:
                return self.upload_storage.read_json_content(upload_id)
            except UploadStorageError as exc:
                raise UploadReferenceResolverError(str(exc)) from exc

        if self.config.use_plugins:
            return self._load_with_plugin(
                kind=kind,
                file_path=file_path,
                metadata=metadata,
            )

        raise UploadReferenceResolverError(
            f"Cannot resolve upload {upload_id}; plugin loading disabled and JSON fallback unavailable."
        )

    def _load_with_plugin(
        self,
        *,
        kind: str,
        file_path: Path,
        metadata: dict[str, Any],
    ) -> Any:
        module_name = (
            self.config.raster_loader_plugin_module
            if kind == "raster"
            else self.config.vector_loader_plugin_module
        )

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise UploadReferenceResolverError(
                f"Could not import {kind} loader plugin '{module_name}': {exc}"
            ) from exc

        if self.config.enforce_loader_contract:
            try:
                return load_with_loader_contract(
                    module_name=module_name,
                    kind=kind,
                    file_path=file_path,
                    options={
                        "upload_metadata": metadata,
                    },
                )
            except LoaderPluginContractError as exc:
                if not self.config.allow_adaptive_loader_fallback:
                    raise UploadReferenceResolverError(str(exc)) from exc

                # Transitional fallback:
                # Existing plugins may still expose older call signatures.
                # Once loader plugins are fully standardized, set
                # allow_adaptive_loader_fallback=False.
                contract_error = exc
            else:
                contract_error = None
        else:
            contract_error = None

        callables = self._candidate_callables(
            module,
            kind=kind,
        )

        if not callables:
            raise UploadReferenceResolverError(
                f"No compatible callable found in plugin '{module_name}'."
            )

        errors: list[str] = []

        for name, func in callables:
            for args, kwargs in self._call_variants(
                file_path=file_path,
                metadata=metadata,
                kind=kind,
            ):
                try:
                    result = func(*args, **kwargs)
                    return _normalize_result(result)
                except TypeError as exc:
                    errors.append(f"{name}: {exc}")
                    continue
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    break

        contract_error_message = ""

        if contract_error is not None:
            contract_error_message = f" Contract error: {contract_error}."

        raise UploadReferenceResolverError(
            f"Plugin '{module_name}' could not load {kind} file '{file_path}'."
            f"{contract_error_message} "
            f"Adaptive errors: {' | '.join(errors[-8:])}"
        )

    @staticmethod
    def _candidate_callables(
        module: Any,
        *,
        kind: str,
    ) -> list[tuple[str, Any]]:
        names_by_kind = {
            "raster": [
                "load_local_raster",
                "load_raster",
                "read_raster",
                "load",
                "run",
                "execute",
            ],
            "vector": [
                "load_local_vector",
                "load_vector",
                "read_vector",
                "load",
                "run",
                "execute",
            ],
        }

        candidates: list[tuple[str, Any]] = []

        for name in names_by_kind[kind]:
            value = getattr(module, name, None)

            if callable(value):
                candidates.append((name, value))

        plugin_obj = getattr(module, "plugin", None)

        if plugin_obj is not None:
            for method_name in [
                "load",
                "run",
                "execute",
                "handle",
                "__call__",
            ]:
                method = getattr(plugin_obj, method_name, None)

                if callable(method):
                    candidates.append((f"plugin.{method_name}", method))

        return candidates

    @staticmethod
    def _call_variants(
        *,
        file_path: Path,
        metadata: dict[str, Any],
        kind: str,
    ) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        path_text = str(file_path)

        payload = {
            "path": path_text,
            "file_path": path_text,
            "input_path": path_text,
            "kind": kind,
            "metadata": metadata,
        }

        return [
            ((), {"path": path_text}),
            ((), {"file_path": path_text}),
            ((), {"input_path": path_text}),
            ((), {"source": path_text}),
            ((path_text,), {}),
            ((payload,), {}),
            ((), payload),
        ]

    @staticmethod
    def _extract_ref(
        inputs: dict[str, Any],
        *,
        direct_key: str,
        object_key: str,
    ) -> str | None:
        direct = inputs.get(direct_key)

        if direct:
            return str(direct)

        obj = inputs.get(object_key)

        if isinstance(obj, dict):
            for key in [
                "upload_id",
                "ref",
                direct_key,
            ]:
                value = obj.get(key)

                if value:
                    return str(value)

        return None


def _normalize_result(value: Any) -> Any:
    if isinstance(value, dict):
        return value

    if hasattr(value, "to_dict") and callable(value.to_dict):
        result = value.to_dict()

        if isinstance(result, dict):
            return result

        return {
            "value": result,
        }

    if is_dataclass(value):
        return asdict(value)

    payload = getattr(value, "__dict__", None)

    if isinstance(payload, dict) and payload:
        return payload

    return value
