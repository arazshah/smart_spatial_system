"""
smart_spatial_system.interfaces.mcp

MCP (Model Context Protocol) server exposing the plugin capability registry
as tools. Install with the ``mcp`` extra: ``pip install "smart_spatial_system[mcp]"``.

This layer adds no analysis logic (ADR-001): every tool is a registered
capability resolved through ``CapabilityRegistry``. Everything here is
secure by default - capabilities that touch the server filesystem, the
network or databases are hidden until an operator explicitly enables their
risk class (see ``policy.py`` / ``config.py``).

Only ``policy``, ``config``, ``catalog``, ``codec`` and ``runtime`` are pure
Python; ``server``/``auth``/``cli`` need the optional ``mcp`` dependency.
"""
