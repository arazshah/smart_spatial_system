"""
orchestrator.planning.capability_resolver

Capability resolvers for DAG execution.

This module bridges DagExecutor to different capability sources:

    - StaticCapabilityResolver:
        Useful for tests and local/manual execution.

    - RegistryCapabilityResolver:
        Adapts a real capability registry/service to the simple callable
        interface expected by DagExecutor.

The resolver returns a callable for a capability name.
"""

from __future__ import annotations

from typing import Any, Callable


class CapabilityResolutionError(LookupError):
    """Raised when a capability cannot be resolved to a callable."""


def _callable_from_candidate(candidate: Any, capability_name: str) -> Callable[..., Any]:
    """
    Normalize a registry candidate into a callable.

    Registries may store:
        - direct function
        - object with .func
        - object with .function
        - object with .handler
        - object with .callable
        - object implementing __call__
    """
    if callable(candidate):
        return candidate

    for attr in ("func", "function", "handler", "callable", "run", "execute"):
        value = getattr(candidate, attr, None)
        if callable(value):
            return value

    raise CapabilityResolutionError(
        f"Resolved capability {capability_name!r}, but it is not callable."
    )


class StaticCapabilityResolver:
    """
    Resolve capabilities from an explicit dict.

    Example:
        resolver = StaticCapabilityResolver({
            "score_features": score_features,
            "rank_features": rank_features,
        })
    """

    def __init__(self, capabilities: dict[str, Callable[..., Any]]) -> None:
        self.capabilities = dict(capabilities)

    def __call__(self, capability_name: str) -> Callable[..., Any]:
        if capability_name not in self.capabilities:
            raise CapabilityResolutionError(
                f"Capability {capability_name!r} not found in static resolver."
            )
        return _callable_from_candidate(
            self.capabilities[capability_name],
            capability_name,
        )


class RegistryCapabilityResolver:
    """
    Resolve capabilities from a registry/service object.

    This adapter is intentionally tolerant because internal registry APIs
    may evolve. It tries common method and attribute names without forcing
    the planning kernel to depend on one concrete registry implementation.

    Supported patterns:
        registry.get_capability(name)
        registry.resolve_capability(name)
        registry.resolve(name)
        registry.get(name)
        registry.capabilities[name]
        registry._capabilities[name]

    Also supports passing a service object with `.registry`.
    """

    METHOD_NAMES = (
        "get_capability",
        "resolve_capability",
        "resolve",
        "get",
        "find",
        "capability",
        "get_registered_capability",
    )

    MAPPING_ATTRS = (
        "capabilities",
        "_capabilities",
        "capability_map",
        "_capability_map",
        "registry",
        "_registry",
    )

    def __init__(self, registry_or_service: Any) -> None:
        self.registry_or_service = registry_or_service

    def __call__(self, capability_name: str) -> Callable[..., Any]:
        candidate = self._resolve_candidate(capability_name)

        if candidate is None:
            raise CapabilityResolutionError(
                f"Capability {capability_name!r} could not be resolved."
            )

        return _callable_from_candidate(candidate, capability_name)

    def _resolve_candidate(self, capability_name: str) -> Any:
        sources = [self.registry_or_service]

        nested_registry = getattr(self.registry_or_service, "registry", None)
        if nested_registry is not None and nested_registry is not self.registry_or_service:
            sources.append(nested_registry)

        for source in sources:
            candidate = self._resolve_from_methods(source, capability_name)
            if candidate is not None:
                return candidate

            candidate = self._resolve_from_mapping_attrs(source, capability_name)
            if candidate is not None:
                return candidate

        return None

    def _resolve_from_methods(self, source: Any, capability_name: str) -> Any:
        for method_name in self.METHOD_NAMES:
            method = getattr(source, method_name, None)
            if not callable(method):
                continue

            try:
                candidate = method(capability_name)
            except (KeyError, LookupError):
                continue
            except TypeError:
                # Some methods with same name may require different signature.
                continue

            if candidate is not None:
                return candidate

        return None

    def _resolve_from_mapping_attrs(self, source: Any, capability_name: str) -> Any:
        for attr in self.MAPPING_ATTRS:
            mapping = getattr(source, attr, None)
            if mapping is None:
                continue

            if isinstance(mapping, dict) and capability_name in mapping:
                return mapping[capability_name]

            # Some registries expose custom mapping-like objects.
            try:
                return mapping[capability_name]
            except Exception:
                pass

        return None
