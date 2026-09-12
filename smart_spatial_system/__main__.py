"""
Enables `python -m smart_spatial_system serve ...` (REFACTOR_PLAN Phase 8,
docs/PHASE8_BACKEND_PACKAGING_CLI_PLAN.md) as an alternative to the
`smart-spatial-api` console script - both call the same
smart_spatial_system.cli.main.
"""

from __future__ import annotations

from smart_spatial_system.cli import main

if __name__ == "__main__":
    main()
