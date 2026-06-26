import inspect

from orchestrator.capability_registry import CapabilityRegistry
from orchestrator.planning.capability_resolver import RegistryCapabilityResolver
from orchestrator.planning.op_catalog import OP_CATALOG


RASTER_OPS = [
    "ndvi",
    "calculate_ndvi",
    "ndvi_from_bands",
    "spectral_index",
    "band_math",
    "raster_threshold",
    "raster_to_vector",
    "raster_reclassify",
    "raster_clip",
    "slope_aspect",
    "zonal_statistics",
    "raster_stats",
]


def _signature_accepts(signature: inspect.Signature, parameter_name: str) -> bool:
    if parameter_name in signature.parameters:
        return True

    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def test_raster_op_catalog_capabilities_resolve_from_registry() -> None:
    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    resolver = RegistryCapabilityResolver(registry)

    for op_name in RASTER_OPS:
        descriptor = OP_CATALOG[op_name]

        binding = registry.resolve(descriptor.capability_name)
        capability_fn = resolver(descriptor.capability_name)

        assert callable(capability_fn)
        assert binding.name == descriptor.capability_name
        assert binding.output_kind == descriptor.output_type


def test_raster_op_catalog_inputs_and_params_match_plugin_signatures() -> None:
    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    resolver = RegistryCapabilityResolver(registry)

    for op_name in RASTER_OPS:
        descriptor = OP_CATALOG[op_name]
        capability_fn = resolver(descriptor.capability_name)
        signature = inspect.signature(capability_fn)

        expected_argument_names = [
            *descriptor.input_map.values(),
            *descriptor.param_map.values(),
        ]

        for argument_name in expected_argument_names:
            assert _signature_accepts(signature, argument_name), (
                f"Operation {op_name!r} maps an argument {argument_name!r} "
                f"to capability {descriptor.capability_name!r}, but plugin "
                f"signature is {signature}"
            )


def test_ndvi_from_bands_registry_capability_is_async() -> None:
    registry = CapabilityRegistry.from_plugin_modules(tolerant=False)
    resolver = RegistryCapabilityResolver(registry)

    descriptor = OP_CATALOG["ndvi_from_bands"]
    capability_fn = resolver(descriptor.capability_name)

    assert descriptor.capability_name == "ndvi_processor"
    assert inspect.iscoroutinefunction(capability_fn) is True
