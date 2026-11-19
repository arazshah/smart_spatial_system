from __future__ import annotations

from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms import ALGORITHMS


class S3GeoProvider(QgsProcessingProvider):
    def id(self) -> str:
        return "s3geo"

    def name(self) -> str:
        return "S3Geo"

    def longName(self) -> str:
        return "S3Geo - Smart Spatial System"

    def icon(self):
        return QIcon(str(Path(__file__).parent / "icon.svg"))

    def loadAlgorithms(self):
        for cls in ALGORITHMS:
            self.addAlgorithm(cls())
