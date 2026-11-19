"""S3Geo - QGIS plugin entry point."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import S3GeoPlugin

    return S3GeoPlugin(iface)
