"""QGIS side of the chat: read layers, add result layers, zoom. All QGIS calls run on the GUI
thread (the agent works in a background thread and goes through MainThread.call)."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsJsonExporter,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QObject, Qt, QThread, pyqtSignal

from .. import core


class MainThread(QObject):
    """Create this on the GUI thread. call(fn) runs fn there and returns its result."""

    _job = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._job.connect(self._run, Qt.QueuedConnection)

    def _run(self, job) -> None:
        fn, fut = job
        try:
            fut.set_result(fn())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            fut.set_exception(exc)

    def call(self, fn: Callable[[], Any], timeout: float = 120.0) -> Any:
        if QThread.currentThread() is self.thread():
            return fn()
        fut: Future = Future()
        self._job.emit((fn, fut))
        return fut.result(timeout)


def _py(value: Any) -> Any:
    from qgis.PyQt.QtCore import QVariant

    if value is None or isinstance(value, QVariant):
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class QgisAdapter:
    def __init__(self, main: MainThread, iface=None) -> None:
        self.main = main
        self.iface = iface

    # -- helpers (GUI thread only)
    def _vector_layers(self) -> list[QgsVectorLayer]:
        return [lyr for lyr in QgsProject.instance().mapLayers().values()
                if isinstance(lyr, QgsVectorLayer)]

    def _find(self, name: str) -> QgsVectorLayer:
        layers = QgsProject.instance().mapLayersByName(name)
        layers = [lyr for lyr in layers if isinstance(lyr, QgsVectorLayer)]
        if not layers:
            have = sorted(lyr.name() for lyr in self._vector_layers())
            raise KeyError(f"No vector layer named {name!r}. Available: {have}")
        if len(layers) > 1:
            raise KeyError(f"Several layers are named {name!r}; ask the user to rename one.")
        return layers[0]

    # -- API used by the agent
    def list_layers(self) -> list[dict[str, Any]]:
        def run():
            return [{"name": lyr.name(), "geometry": QgsWkbTypes.displayString(lyr.wkbType()),
                     "crs": lyr.crs().authid(), "feature_count": lyr.featureCount(),
                     "selected": lyr.selectedFeatureCount()} for lyr in self._vector_layers()]
        return self.main.call(run)

    def layer_info(self, name: str) -> dict[str, Any]:
        def run():
            lyr = self._find(name)
            ext = QgsCoordinateTransform(lyr.crs(), QgsCoordinateReferenceSystem("EPSG:4326"),
                                         QgsProject.instance()).transformBoundingBox(lyr.extent())
            rows = []
            for i, f in enumerate(lyr.getFeatures()):
                if i >= 3:
                    break
                rows.append({fld.name(): _py(f[fld.name()]) for fld in lyr.fields()})
            return {"name": lyr.name(), "geometry": QgsWkbTypes.displayString(lyr.wkbType()),
                    "crs": lyr.crs().authid(), "feature_count": lyr.featureCount(),
                    "selected": lyr.selectedFeatureCount(),
                    "fields": [{"name": f.name(), "type": f.typeName()} for f in lyr.fields()],
                    "extent_wgs84": [round(ext.xMinimum(), 5), round(ext.yMinimum(), 5),
                                     round(ext.xMaximum(), 5), round(ext.yMaximum(), 5)],
                    "sample_rows": rows}
        return self.main.call(run)

    def layer_geojson(self, name: str, selected_only: bool = False) -> dict[str, Any]:
        def run():
            lyr = self._find(name)
            exporter = QgsJsonExporter()
            exporter.setSourceCrs(lyr.crs())
            exporter.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
            feats = lyr.selectedFeatures() if selected_only else list(lyr.getFeatures())
            if selected_only and not feats:
                raise ValueError(f"Layer {name!r} has no selected features.")
            return json.loads(exporter.exportFeatures(feats))
        return self.main.call(run)

    def add_layer(self, fc: dict[str, Any], name: str) -> str:
        def run():
            unique, n = name, 2
            while QgsProject.instance().mapLayersByName(unique):
                unique = f"{name}_{n}"
                n += 1
            layer, _tmp = core.geojson_to_layer(fc, unique)
            layer.setName(unique)
            QgsProject.instance().addMapLayer(layer)
            return unique
        return self.main.call(run)

    def zoom_to(self, name: str) -> None:
        def run():
            lyr = self._find(name)
            if self.iface is None:
                return
            canvas = self.iface.mapCanvas()
            ext = QgsCoordinateTransform(lyr.crs(), canvas.mapSettings().destinationCrs(),
                                         QgsProject.instance()).transformBoundingBox(lyr.extent())
            canvas.setExtent(ext)
            canvas.refresh()
        self.main.call(run)
