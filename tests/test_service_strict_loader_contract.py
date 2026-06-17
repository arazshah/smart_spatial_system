from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.service import OrchestratorService, OrchestratorServiceConfig  # noqa: E402


def test_service_can_enable_strict_loader_contract(tmp_path: Path) -> None:
    service = OrchestratorService(
        OrchestratorServiceConfig(
            uploads_path=tmp_path / "uploads",
            outputs_path=tmp_path / "outputs",
            weights_path=tmp_path / "weights" / "router_weights.json",
            enforce_loader_contract=True,
            allow_adaptive_loader_fallback=False,
        )
    )

    assert service.config.enforce_loader_contract is True
    assert service.config.allow_adaptive_loader_fallback is False
