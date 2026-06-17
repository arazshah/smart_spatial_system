# Plugin Quality Gates

## Gate 1: File Presence
- Verify all required files exist:
  - `plugin.py`
  - `manifest.json`
  - `config.yaml`
  - `tests/test_plugin.py`
  - `spec.yaml`

## Gate 2: Import Safety
- All imports resolve without errors
- No unauthorized external packages
- Only GeoChat SDK imports used

## Gate 3: Manifest Validity
- Manifest is valid JSON
- All required fields present
- plugin_id matches filename
- Capabilities list is non-empty

## Gate 4: Capability Registration
- Each capability has unique name
- Keywords include multilingual support
- Required inputs are documented
- Output kind is valid (raster/vector)

## Gate 5: Descriptor Build
- Plugin can be instantiated
- Manifest property returns valid object
- No exceptions during initialization

## Gate 6: Config Safety
- All config keys have defaults
- Config validation passes with defaults
- Invalid config raises appropriate error

## Gate 7: Runtime Overrides Config
- Runtime parameters correctly override config
- Defaults used when no override provided
- Type validation on override values

## Gate 8: Metadata Completeness
- plugin_id, name, version, author, goal all present
- Metadata matches spec.yaml
- No missing required fields

## Gate 9: Error Handling
- Custom exception classes used
- User-friendly error messages
- Errors logged with context
- No stack trace exposure

## Gate 10: Nodata Handling
- Nodata values are handled explicitly
- Nodata propagation is documented
- Tests cover nodata scenarios

## Gate 11: Dedicated Tests Pass
- All 10 test categories pass
- Test coverage meets minimum threshold
- No skipped or xfailed tests

## Gate 12: Related Regression Tests Pass
- Existing plugin tests still pass
- No breaking changes to GeoChat
- Integration tests pass

## Gate 13: No Unauthorized Dependencies
- Only approved imports used
- No external package dependencies
- Dependency list matches manifest

## Gate 14: No Kernel/SDK Mutation
- Plugin does not modify GeoChat kernel
- No monkey-patching of SDK classes
- All operations isolated to plugin scope

## Gate 15: Report Generated
- Generation report exists
- Report contains all required sections
- Report is in valid markdown format
