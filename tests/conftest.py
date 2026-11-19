from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qgis_app():
    """One headless QGIS (GUI classes enabled, offscreen) with the S3Geo provider registered."""
    pytest.importorskip("qgis.core")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(PROJECT_ROOT / "qgis_plugin"))
    sys.path.append("/usr/share/qgis/python/plugins")  # the processing module (Debian/Ubuntu)
    from qgis.core import QgsApplication

    app = QgsApplication([], True)
    app.initQgis()
    from processing.core.Processing import Processing

    Processing.initialize()
    from s3geo_processing.provider import S3GeoProvider

    provider = S3GeoProvider()
    QgsApplication.processingRegistry().addProvider(provider)
    yield app
    try:
        QgsApplication.processingRegistry().removeProvider(provider)
    except RuntimeError:  # removed (and deleted) by test_plugin_object_loads_and_unloads
        pass
    app.exitQgis()
