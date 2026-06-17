# Plugin Generation Checklist

## Input
- [ ] Plugin ID is provided and unique
- [ ] Goal is clear and concise
- [ ] Priority is defined (if applicable)
- [ ] Constraints are documented

## PluginSpec
- [ ] plugin_id matches input
- [ ] name is human-readable
- [ ] version is set
- [ ] goal matches input
- [ ] category is appropriate
- [ ] data_type is correct
- [ ] operation name is defined
- [ ] capabilities list is complete
- [ ] config schema is defined

## Files
- [ ] PluginSpec YAML created
- [ ] Plugin Python file created
- [ ] Test Python file created
- [ ] Report file created

## Plugin Code
- [ ] Inherits from BasePlugin
- [ ] Implements manifest property
- [ ] Implements initialize method
- [ ] Implements execute method
- [ ] Error handling with custom exceptions
- [ ] Config validation implemented
- [ ] Capabilities registered in manifest
- [ ] Output follows raster/vector rules
- [ ] No kernel/SDK mutation
- [ ] Code compiles without errors

## Config
- [ ] All keys have defaults
- [ ] Config validation implemented
- [ ] Runtime parameters override config
- [ ] Config is immutable during execution
- [ ] Invalid config raises error
- [ ] Config schema documented
- [ ] Default config works out of box

## Tests
- [ ] Import test exists
- [ ] Manifest test exists
- [ ] Capability test exists
- [ ] Config test exists
- [ ] Execution test exists
- [ ] Error test exists
- [ ] Edge case test exists
- [ ] Nodata test exists
- [ ] Performance test exists
- [ ] Integration test exists

## Test Execution
- [ ] All tests pass
- [ ] No test failures
- [ ] Test coverage is adequate
- [ ] Tests run within time limit

## Report
- [ ] Report is generated
- [ ] Plugin ID is correct
- [ ] Goal is documented
- [ ] Generated files are listed
- [ ] Capabilities are documented
- [ ] Config keys are listed
- [ ] Test results are recorded
- [ ] Known limitations are documented

## Final Decision
- [ ] All quality gates pass
- [ ] Human review is complete
- [ ] Plugin is ready for deployment
