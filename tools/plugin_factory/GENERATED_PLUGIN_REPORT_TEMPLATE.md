# Plugin Generation Report

## Plugin ID
`{plugin_id}`

## Goal
{goal}

## Generated Files
- `plugins/{plugin_id}/plugin.py`
- `plugins/{plugin_id}/manifest.json`
- `plugins/{plugin_id}/config.yaml`
- `plugins/{plugin_id}/tests/test_plugin.py`
- `plugins/{plugin_id}/spec.yaml`

## Capabilities
| Capability Name | Keywords | Required Inputs | Optional Inputs | Output Kind |
|----------------|----------|----------------|----------------|-------------|
| {capability_1} | {keywords} | {required} | {optional} | {output} |

## Config Keys
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| {key_1} | {type} | {default} | {description} |

## Tests Created
- test_import
- test_manifest
- test_capability_registration
- test_config_defaults
- test_execution
- test_error_handling
- test_edge_cases
- test_nodata_handling
- test_performance
- test_integration

## Commands Run
```bash
pytest plugins/{plugin_id}/tests/ -v
```

## Test Result
- Total: 10
- Passed: 10
- Failed: 0
- Skipped: 0

## Failures Encountered
- None

## Fixes Applied
- None required

## Known Limitations
- {limitation_1}
- {limitation_2}

## Integration Status
- [ ] Plugin registered in registry
- [ ] Plugin loaded by GeoChat
- [ ] Plugin executed successfully
- [ ] Output validated

## Next Suggested Work
- {suggestion_1}
- {suggestion_2}
