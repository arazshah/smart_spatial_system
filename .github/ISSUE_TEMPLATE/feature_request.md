---
name: Feature request
about: A new plugin, operation, or capability
title: ""
labels: enhancement
---

**What are you trying to do** that the system can't do today?

**Does this fit the existing pipeline** (QuerySpec → DeterministicPlanner → DAG → plugin), or does it need something new? See [ADR-001](../../docs/ADR-001-single-kernel-pipeline.md) — new functionality should enter as a plugin/OP_CATALOG entry, not a new direct handler.

**Proposed shape**, if you have one: a new plugin, a new operation on an existing plugin, a new data source connector, something else.

**Would you be able to contribute this yourself?** (Not required — just helps prioritize.)
