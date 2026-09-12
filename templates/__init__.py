"""
templates

Not application code — this package exists only so the Jinja2 report
templates under `templates/reports/` can be bundled as package data and
located via `importlib.resources` after a `pip install`, the same way
`config/__init__.py` does for default plugin configuration. See
`plugins/pdf_renderer.py::_resolve_templates_dir`.
"""
