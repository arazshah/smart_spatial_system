from __future__ import annotations

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from . import core
from .provider import S3GeoProvider


class S3GeoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802 (QGIS API name)
        self.provider = S3GeoProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802 (QGIS API name)
        self.initProcessing()
        self.action = QAction("S3Geo: check installation", self.iface.mainWindow())
        self.action.triggered.connect(self.check_installation)
        self.iface.addPluginToMenu("S3Geo", self.action)
        try:
            core.require_s3geo()
        except core.S3GeoUnavailable as exc:
            self.iface.messageBar().pushMessage(
                "S3Geo", "The smart_spatial_system Python package is missing. Open "
                "Plugins > S3Geo > check installation.", level=Qgis.Warning, duration=10)
            self._last_error = str(exc)

    def check_installation(self):
        try:
            core.require_s3geo()
            n = len(core.registry().registered_capability_names())
            ver = ".".join(map(str, core.package_version() or ()))
            QMessageBox.information(self.iface.mainWindow(), "S3Geo",
                                    f"smart_spatial_system {ver} is available: {n} capabilities.")
        except core.S3GeoUnavailable as exc:
            QMessageBox.warning(self.iface.mainWindow(), "S3Geo", str(exc))

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        if self.action is not None:
            self.iface.removePluginMenu("S3Geo", self.action)
            self.action = None
