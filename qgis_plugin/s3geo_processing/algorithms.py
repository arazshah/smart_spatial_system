"""Processing algorithms. Each one is a form around a capability; no analysis logic lives here."""

from __future__ import annotations

import json
from typing import Any

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import core

_METRIC_NOTE = (
    "\n\nDistances are in metres: the layers are reprojected to the UTM zone of the data, "
    "analysed, and the result is returned in EPSG:4326."
)


class S3Algorithm(QgsProcessingAlgorithm):
    NAME = ""
    DISPLAY = ""
    HELP = ""

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("S3Geo", text)

    def name(self) -> str:
        return self.NAME

    def displayName(self) -> str:
        return self.tr(self.DISPLAY)

    def group(self) -> str:
        return self.tr("S3Geo")

    def groupId(self) -> str:
        return "s3geo"

    def shortHelpString(self) -> str:
        return self.tr(self.HELP)

    def helpUrl(self) -> str:
        return "https://s3geo.com"

    def createInstance(self):
        return type(self)()

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    # subclasses implement analyse(); errors become readable Processing errors
    def processAlgorithm(self, parameters, context, feedback):
        try:
            core.require_s3geo()
            return self.analyse(parameters, context, feedback)
        except QgsProcessingException:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any capability error to the user
            raise QgsProcessingException(f"{type(exc).__name__}: {exc}") from exc

    def analyse(self, parameters, context, feedback) -> dict[str, Any]:
        raise NotImplementedError

    def _warn(self, fc: dict[str, Any], feedback) -> None:
        for w in (fc.get("metadata") or {}).get("warnings") or []:
            feedback.pushWarning(str(w))
        if not fc.get("features"):
            feedback.pushWarning("The analysis returned no features.")

    def _finish(self, parameters, context, fc, feedback) -> dict[str, Any]:
        self._warn(fc, feedback)
        return {"OUTPUT": core.write_sink(self, parameters, "OUTPUT", context, fc)}

    def _geojson(self, parameters, name, context):
        source = self.parameterAsSource(parameters, name, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, name))
        return core.source_to_geojson(source)


class NearestFeature(S3Algorithm):
    NAME = "nearest_feature"
    DISPLAY = "Nearest feature (distance in metres)"
    HELP = (
        "For every feature of the input layer, finds the K nearest features of the target layer "
        "and writes the distance in metres (_nearest_distance) and the target attributes "
        "(_target_properties, JSON)." + _METRIC_NOTE
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource("INPUT", "Input layer"))
        self.addParameter(QgsProcessingParameterFeatureSource("TARGET", "Target layer"))
        self.addParameter(QgsProcessingParameterNumber(
            "K", "Neighbours per feature", QgsProcessingParameterNumber.Integer, 1, minValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            "MAX_DISTANCE", "Maximum distance in metres (0 = no limit)",
            QgsProcessingParameterNumber.Double, 0, minValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Nearest feature"))

    def analyse(self, parameters, context, feedback):
        max_d = self.parameterAsDouble(parameters, "MAX_DISTANCE", context)
        fc = core.run_capability(
            "find_nearest_neighbors", metric=True,
            source_features=self._geojson(parameters, "INPUT", context),
            target_features=self._geojson(parameters, "TARGET", context),
            k=self.parameterAsInt(parameters, "K", context),
            max_distance=max_d if max_d > 0 else None)
        return self._finish(parameters, context, fc, feedback)


class BufferMetres(S3Algorithm):
    NAME = "buffer_metres"
    DISPLAY = "Buffer (distance in metres)"
    HELP = "Buffers every feature by a distance in metres, whatever the layer CRS." + _METRIC_NOTE

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource("INPUT", "Input layer"))
        self.addParameter(QgsProcessingParameterNumber(
            "DISTANCE", "Distance (metres)", QgsProcessingParameterNumber.Double, 500,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterBoolean("DISSOLVE", "Dissolve into one feature",
                                                        False))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Buffer"))

    def analyse(self, parameters, context, feedback):
        fc = core.run_capability(
            "buffer_vector_features", metric=True,
            features=self._geojson(parameters, "INPUT", context),
            distance=self.parameterAsDouble(parameters, "DISTANCE", context), units="meters",
            dissolve=self.parameterAsBool(parameters, "DISSOLVE", context))
        return self._finish(parameters, context, fc, feedback)


class AreaPerimeter(S3Algorithm):
    NAME = "area_perimeter"
    DISPLAY = "Area and perimeter (m², m)"
    HELP = "Adds _area (square metres) and _perimeter (metres) to every feature." + _METRIC_NOTE

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "INPUT", "Polygon or line layer",
            [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "With area and perimeter"))

    def analyse(self, parameters, context, feedback):
        fc = core.run_capability("calculate_area_perimeter", metric=True,
                                 features=self._geojson(parameters, "INPUT", context))
        return self._finish(parameters, context, fc, feedback)


class PointsInPolygons(S3Algorithm):
    NAME = "points_in_polygons"
    DISPLAY = "Points inside polygons"
    HELP = "Keeps the points that fall inside any polygon of the second layer."

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "POINTS", "Points", [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "POLYGONS", "Polygons", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterBoolean(
            "DROP_OUTSIDE", "Drop points outside (otherwise flag them)", True))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Points"))

    def analyse(self, parameters, context, feedback):
        fc = core.run_capability(
            "filter_points_in_polygon", metric=False,
            points=self._geojson(parameters, "POINTS", context),
            polygons=self._geojson(parameters, "POLYGONS", context),
            drop_outside=self.parameterAsBool(parameters, "DROP_OUTSIDE", context))
        return self._finish(parameters, context, fc, feedback)


class Hotspots(S3Algorithm):
    NAME = "hotspots"
    DISPLAY = "Hotspot analysis (Getis-Ord Gi*)"
    HELP = (
        "Statistical hot and cold spots of a numeric field, using the K nearest neighbours as "
        "spatial weights. Adds the Gi* z-score, p-value and class to every feature."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource("INPUT", "Input layer"))
        self.addParameter(QgsProcessingParameterField(
            "FIELD", "Numeric field", parentLayerParameterName="INPUT",
            type=QgsProcessingParameterField.Numeric))
        self.addParameter(QgsProcessingParameterNumber(
            "K", "Neighbours", QgsProcessingParameterNumber.Integer, 8, minValue=2))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Hotspots"))

    def analyse(self, parameters, context, feedback):
        fc = core.run_capability(
            "calculate_hotspots", metric=True,
            features=self._geojson(parameters, "INPUT", context),
            field=self.parameterAsString(parameters, "FIELD", context),
            k=self.parameterAsInt(parameters, "K", context))
        return self._finish(parameters, context, fc, feedback)


class Clusters(S3Algorithm):
    NAME = "dbscan_clusters"
    DISPLAY = "Clusters (DBSCAN, distance in metres)"
    HELP = (
        "Density-based clustering. Features within EPS metres of each other, in groups of at "
        "least MIN SAMPLES, share a cluster id; isolated features are noise." + _METRIC_NOTE
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "INPUT", "Point layer", [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterNumber(
            "EPS", "Neighbourhood radius EPS (metres)", QgsProcessingParameterNumber.Double, 500,
            minValue=0.001))
        self.addParameter(QgsProcessingParameterNumber(
            "MIN_SAMPLES", "Minimum samples", QgsProcessingParameterNumber.Integer, 5,
            minValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Clusters"))

    def analyse(self, parameters, context, feedback):
        fc = core.run_capability(
            "cluster_features", metric=True,
            features=self._geojson(parameters, "INPUT", context), method="dbscan",
            eps=self.parameterAsDouble(parameters, "EPS", context),
            min_samples=self.parameterAsInt(parameters, "MIN_SAMPLES", context))
        return self._finish(parameters, context, fc, feedback)


def _substitute(value: Any, placeholders: dict[str, Any]) -> Any:
    if isinstance(value, str) and value in placeholders:
        return placeholders[value]
    if isinstance(value, list):
        return [_substitute(v, placeholders) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, placeholders) for k, v in value.items()}
    return value


class RunCapability(S3Algorithm):
    NAME = "run_capability"
    DISPLAY = "Run any capability (advanced)"
    HELP = (
        "Runs one of the 100 capabilities by name. PARAMETERS is a JSON object; the strings "
        '"@A" and "@B" are replaced by the GeoJSON of Layer A / Layer B, e.g. '
        '{"features": "@A", "distance": 300, "units": "meters"} for buffer_vector_features. '
        "List the names with: python -c \"import s3geo; "
        "print(s3geo.registry().registered_capability_names())\"."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            "CAPABILITY", "Capability name", "buffer_vector_features"))
        self.addParameter(QgsProcessingParameterFeatureSource("LAYER_A", "Layer A (@A)"))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "LAYER_B", "Layer B (@B)", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "PARAMETERS", "Parameters (JSON)", '{"features": "@A", "distance": 500, '
            '"units": "meters"}', multiLine=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "METRIC", "Reproject to UTM first and return EPSG:4326", True))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Result"))

    def analyse(self, parameters, context, feedback):
        name = self.parameterAsString(parameters, "CAPABILITY", context).strip()
        if name not in core.registry().registered_capability_names():
            raise QgsProcessingException(f"Unknown capability: {name!r}")
        try:
            params = json.loads(self.parameterAsString(parameters, "PARAMETERS", context) or "{}")
        except json.JSONDecodeError as exc:
            raise QgsProcessingException(f"PARAMETERS is not valid JSON: {exc}") from exc
        if not isinstance(params, dict):
            raise QgsProcessingException("PARAMETERS must be a JSON object.")
        placeholders = {"@A": self._geojson(parameters, "LAYER_A", context)}
        if parameters.get("LAYER_B"):
            placeholders["@B"] = self._geojson(parameters, "LAYER_B", context)
        fc = core.run_capability(
            name, metric=self.parameterAsBool(parameters, "METRIC", context),
            **_substitute(params, placeholders))
        if not core.is_feature_collection(fc) and not (isinstance(fc, dict) and "features" in fc):
            feedback.pushInfo(json.dumps(fc, ensure_ascii=False, default=str)[:4000])
            raise QgsProcessingException(
                f"{name} does not return a vector layer; its result is printed in the log.")
        return self._finish(parameters, context, fc, feedback)


class AskQuestion(S3Algorithm):
    NAME = "ask_question"
    DISPLAY = "Ask a question (natural language, experimental)"
    HELP = (
        "Plans and runs an analysis from a question written in plain language, using the layers "
        "you select (referred to by layer name). Needs an LLM: set LLM_API_KEY (and optionally "
        "LLM_BASE_URL / LLM_DEFAULT_MODEL) in the environment QGIS was started from. The layer "
        "names, field names and extent are sent to the LLM provider."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            "QUESTION", "Question", "Which homes are within 500 m of a metro station?",
            multiLine=True))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "LAYERS", "Layers", QgsProcessing.TypeVectorAnyGeometry))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", "Result (if the answer is a layer)", optional=True))

    def analyse(self, parameters, context, feedback):
        s3geo = core.require_s3geo()
        layers = {}
        for layer in self.parameterAsLayerList(parameters, "LAYERS", context):
            source = layer
            from qgis.core import QgsCoordinateReferenceSystem, QgsJsonExporter

            exporter = QgsJsonExporter()
            exporter.setSourceCrs(source.crs())
            exporter.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
            layers[layer.name()] = json.loads(exporter.exportFeatures(list(source.getFeatures())))
        if not layers:
            raise QgsProcessingException("Select at least one layer.")
        feedback.pushInfo("Planning with the LLM...")
        result = s3geo.query(self.parameterAsString(parameters, "QUESTION", context),
                             layers=layers)
        feedback.pushInfo(f"Goal: {result.goal}")
        feedback.pushInfo("Operations: " + " -> ".join(result.operations))
        from smart_spatial_system.shared.jsonable import to_jsonable

        out = to_jsonable(result.output)
        if isinstance(out, dict) and out.get("features") is not None:
            out = core.as_feature_collection({"type": "FeatureCollection", **out})
            self._warn(out, feedback)
            return {"OUTPUT": core.write_sink(self, parameters, "OUTPUT", context, out)}
        feedback.pushInfo("The answer is not a vector layer: " +
                          json.dumps(out, ensure_ascii=False, default=str)[:4000])
        return {}


ALGORITHMS = [NearestFeature, BufferMetres, AreaPerimeter, PointsInPolygons, Hotspots, Clusters,
              RunCapability, AskQuestion]
