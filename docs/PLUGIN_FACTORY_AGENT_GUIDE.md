# Plugin Factory Agent Guide

## Purpose
This guide defines the standards, patterns, and processes for generating production-ready plugins for the GeoChat platform using an agentic workflow.

## Project Areas
- **Plugin Factory**: Tools and templates for plugin generation
- **Plugin Registry**: Central repository of all plugins
- **GeoChat Kernel**: Core platform that loads and executes plugins

## Existing Plugin Pattern
All plugins follow a consistent pattern:
1. A plugin class inheriting from `BasePlugin`
2. A manifest defining metadata and capabilities
3. Configuration schema with validation
4. Error handling with structured error codes
5. Comprehensive test suite

## Required Imports
```python
from typing import Any, Dict, List, Optional
from geochat.core.plugin_base import BasePlugin
from geochat.core.manifest import PluginManifest
from geochat.core.capability import Capability
from geochat.core.config import PluginConfig
from geochat.core.errors import PluginError, ValidationError
```

## Capability Design
Each capability must:
- Have a unique name
- Define required and optional inputs
- Specify output kind (raster/vector)
- Include multilingual keywords
- Be registered in the manifest

## Plugin Metadata Standards
- `plugin_id`: Unique identifier (snake_case)
- `name`: Human-readable name
- `version`: Semantic versioning
- `author`: Creator/team name
- `goal`: One-sentence description

## Config Rules
- All config keys must have defaults
- Config must be validated on load
- Runtime parameters override config defaults
- Config must be immutable during execution

## Error Handling Rules
- Use custom exception classes
- Always provide user-friendly error messages
- Log errors with context
- Never expose stack traces to users

## Dependency Rules
- Only use approved GeoChat SDK imports
- No external packages without approval
- Dependencies must be declared in manifest

## Raster Plugin Output Rule
- Output must be a GeoChat Raster object
- Must preserve CRS and geotransform
- Nodata handling must be explicit

## Vector Plugin Output Rule
- Output must be a GeoChat VectorLayer object
- Must preserve schema and CRS
- Geometry validation required

## Test Rules (10 required test categories)
1. **Import Tests**: Verify all imports work
2. **Manifest Tests**: Validate manifest structure
3. **Capability Tests**: Test capability registration
4. **Config Tests**: Test config defaults and overrides
5. **Execution Tests**: Test main plugin logic
6. **Error Tests**: Test error handling
7. **Edge Case Tests**: Test empty/null inputs
8. **Nodata Tests**: Test nodata handling
9. **Performance Tests**: Test with large datasets
10. **Integration Tests**: Test with real GeoChat components

## Agentic Generation Workflow (7-step loop)
1. **Input**: Receive plugin name and goal
2. **Spec**: Generate PluginSpec YAML
3. **Code**: Generate plugin Python code
4. **Tests**: Generate test suite
5. **Quality**: Run quality gates
6. **Report**: Generate generation report
7. **Review**: Human review and approval

## Definition of Done (13 checkboxes)
- [ ] PluginSpec YAML is complete
- [ ] Plugin code compiles without errors
- [ ] All 10 test categories pass
- [ ] Quality gates pass (15/15)
- [ ] Report is generated
- [ ] No unauthorized dependencies
- [ ] Config is validated
- [ ] Error handling is complete
- [ ] Capabilities are registered
- [ ] Manifest is valid
- [ ] Output follows raster/vector rules
- [ ] Documentation is generated
- [ ] Human review is complete

## Name+Goal Input Contract
Input must include:
- `plugin_id`: Unique identifier
- `goal`: Clear one-sentence description
- Optional: priority, constraints

## Important Architectural Rule
Plugins must never modify the GeoChat kernel or SDK. All operations must be isolated to the plugin's own scope.
