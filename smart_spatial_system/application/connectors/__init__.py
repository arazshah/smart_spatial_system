"""
smart_spatial_system.application.connectors

Connector contract layer (ADR-004 phase 7, docs/CONNECTOR_REGISTRY_DESIGN.md).

A *connector* is an ordinary registered capability that declares a ``connector``
block in its ``@capability(metadata=...)``. This package indexes those capabilities
(``ConnectorRegistry``) and normalizes what they return (``ConnectorResult``), so API
routes, the planner, the MCP server and the UI can treat every source the same way.
It never imports plugin modules: it only talks to the ``CapabilityRegistry``.
"""

from .registry import ConnectorError, ConnectorRegistry, ConnectorSpec
from .result import ConnectorResult

__all__ = ["ConnectorError", "ConnectorRegistry", "ConnectorResult", "ConnectorSpec"]
