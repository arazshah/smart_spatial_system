# Phase 6 / Step 1 — Repository Hygiene Plan

## Status

Planned.

## Purpose

The purpose of this step is to define a safe repository hygiene plan before
starting backend architecture decomposition.

The project has grown significantly and now contains source code, runtime data,
generated artifacts, backup files, frontend build outputs, and temporary files
in the same working tree.

Before extracting internal services and cleaning architecture boundaries, we
need a clear plan for what belongs in source control and what should be ignored,
archived, moved, or regenerated.

This step is intentionally a planning step.

No large deletion or migration should happen in this step.

## Why This Matters

The backend is moving toward a service-oriented modular architecture.

Phase 6 will extract responsibilities from `orchestrator/service.py` into
smaller internal services. During this process, repository noise can cause
confusion:

- backup files can appear in grep/search results
- runtime outputs can hide source files
- generated reports can look like product assets
- frontend build outputs can pollute backend audits
- local uploads/projects/outputs can make tests and manual runs hard to reason
  about

A clean repository structure is required before deeper refactoring.

## Current Observed Repository Noise

The current repository contains several categories of non-source or noisy files.

### 1. Backup and Temporary Files

Observed patterns:

```text
*.bak
*.bak.*
*.before-*.bak
*.old
*backup*
    
    
  
  
Examples:

        
        text
        
    
  
      api/main.py.before-fix-project-id.bak
orchestrator/service.py.bak.phase2_step1
orchestrator/service.py.before-real-estate-ranking-bridge.bak
orchestrator/planning/llm_spec_generator.py.before-*.bak
plugins/postgis_connector.py.before-*.bak
frontend/src/App.jsx.bak.*
    
    
  
  
Risk:

        
        text
        
    
  
      - unclear source of truth
- noisy grep results
- harder architecture audit
- possible developer confusion
    
    
  
  
Plan:

        
        text
        
    
  
      - Do not keep backup files inside active source directories.
- Move valuable historical backups to an archive outside active imports/searches,
  or rely on git history.
- Add ignore rules to prevent new backup files from entering the repo.
    
    
  
  
2. Runtime Output Directories

Observed directories:

        
        text
        
    
  
      outputs/
output/
artifacts/reports/
cache/
    
    
  
  
Examples:

        
        text
        
    
  
      outputs/req-*/
artifacts/reports/real_estate_ranking_*.pdf
cache/plugins/
    
    
  
  
Risk:

        
        text
        
    
  
      - generated runtime state mixed with source code
- huge repo over time
- confusing audits
- test/manual execution pollution
    
    
  
  
Plan:

        
        text
        
    
  
      - Standardize runtime output paths in Phase 6 / Step 2.
- Prefer a single runtime root such as var/.
- Move future generated outputs under runtime root.
- Add generated output directories to .gitignore.
    
    
  
  
Target example:

        
        text
        
    
  
      var/
  outputs/
  reports/
  cache/
    
    
  
  
3. Upload and Project Runtime State

Observed directories:

        
        text
        
    
  
      uploads/
projects/
    
    
  
  
Examples:

        
        text
        
    
  
      uploads/upl-*/
projects/prj-*/
    
    
  
  
Risk:

        
        text
        
    
  
      - runtime state mixed with source
- manual and test data difficult to distinguish
- project/upload state can grow unbounded
    
    
  
  
Plan:

        
        text
        
    
  
      - Treat uploads and projects as runtime state.
- Move future uploads/projects under runtime root.
- Keep test fixtures separate from runtime storage.
    
    
  
  
Target example:

        
        text
        
    
  
      var/
  uploads/
  projects/
    
    
  
  
4. Frontend Generated or Vendor Files

Observed directories:

        
        text
        
    
  
      frontend/node_modules/
frontend/dist/
    
    
  
  
Risk:

        
        text
        
    
  
      - large non-source directories
- noisy repository scans
- irrelevant to backend Phase 6
    
    
  
  
Plan:

        
        text
        
    
  
      - Keep frontend source if needed.
- Ignore frontend/node_modules.
- Ignore frontend/dist unless intentionally publishing build artifacts.
- Exclude frontend generated paths from backend architecture audits.
    
    
  
  
5. Configuration Backups

Observed examples:

        
        text
        
    
  
      config/plugins/postgis_connector.yaml.before-*.bak
config/plugins/.backups
    
    
  
  
Risk:

        
        text
        
    
  
      - accidental credential leakage
- unclear active config
- noisy plugin config directory
    
    
  
  
Plan:

        
        text
        
    
  
      - Keep only active config and example config files in config/plugins.
- Move backups outside active config paths or rely on git history.
- Ensure secrets/credentials are not committed.
- Use .env or secrets path for sensitive runtime values.
    
    
  
  
6. Generated Documents Specific to One Use Case

Observed examples:

        
        text
        
    
  
      artifacts/reports/real_estate_ranking_*.pdf
    
    
  
  
Risk:

        
        text
        
    
  
      - use-case-specific generated artifacts appear as core product files
- reinforces real-estate leakage into generic backend structure
    
    
  
  
Plan:

        
        text
        
    
  
      - Treat generated reports as runtime artifacts.
- Move report outputs under runtime root.
- Keep report templates separately under templates/reports.
- Move real-estate report generation logic out of core in Phase 6.
    
    
  
  
Source-Control Policy

The repository should contain:

        
        text
        
    
  
      - source code
- tests
- documentation
- examples
- templates
- safe example configuration
- migration/utility scripts
    
    
  
  
The repository should not contain:

        
        text
        
    
  
      - runtime outputs
- uploaded files
- generated project state
- generated reports
- cache files
- node_modules
- frontend build output unless explicitly required
- local backup files
- secrets or credentials
    
    
  
  
Proposed Runtime Directory Standard

Phase 6 / Step 2 should introduce or formalize a runtime root.

Preferred target:

        
        text
        
    
  
      var/
  outputs/
  uploads/
  projects/
  reports/
  cache/
    
    
  
  
Alternative acceptable names:

        
        text
        
    
  
      runtime/
.local/
storage/
    
    
  
  
Decision for now:

        
        text
        
    
  
      Use var/ as the default local runtime root unless a stronger reason appears.
    
    
  
  
Reason:

        
        text
        
    
  
      - common convention for runtime/generated state
- short and clear
- easy to ignore
- separates source from runtime files
    
    
  
  
Proposed .gitignore Additions

The following patterns should be considered in Phase 6 / Step 2:

        
        jboss-cli
        
    
  
      # Runtime state
/var/
/outputs/
/output/
/uploads/
/projects/
/cache/
/artifacts/reports/

# Backup files
*.bak
*.bak.*
*.before-*.bak
*.old
*backup*

# Frontend generated/vendor files
/frontend/node_modules/
/frontend/dist/

# Python generated files
__pycache__/
*.py[cod]
.pytest_cache/

# Local env/secrets
.env
.env.*
/secrets/
    
    
  
  
Important:

        
        text
        
    
  
      Before applying ignore rules, check whether any currently tracked files would be
affected and whether they should remain tracked.
    
    
  
  
Use:

        
        bash
        
    
  
      git ls-files
git status --short
    
    
  
  
Safe Cleanup Strategy

Cleanup must be incremental and reversible.

Step A — Document and ignore

First:

        
        text
        
    
  
      - document the hygiene policy
- update .gitignore
- do not delete large sets of files blindly
    
    
  
  
Step B — Inspect tracked noisy files

Use:

        
        bash
        
    
  
      git ls-files | grep -E '(\.bak|\.before-|^outputs/|^uploads/|^projects/|^cache/|^output/|^artifacts/reports/|^frontend/node_modules/|^frontend/dist/)'
    
    
  
  
This tells us which noisy files are tracked.

Step C — Decide per category

For each category:

        
        text
        
    
  
      - keep
- move to docs/archive
- move outside repository
- remove from git but keep locally
- delete completely if generated and reproducible
    
    
  
  
Step D — Remove from git safely if needed

If files are already tracked but should remain locally:

        
        bash
        
    
  
      git rm --cached <path>
    
    
  
  
Do not use this blindly on many paths without review.

Step E — Run tests

After each cleanup step:

        
        bash
        
    
  
      PYTHONPATH=. pytest -q
    
    
  
  
or relevant focused tests.

Step F — Commit small changes

Each cleanup category should be its own commit.

Audit Commands for Phase 6

List noisy backup files

        
        bash
        
    
  
      find . \
  \( -name "*.bak*" -o -name "*.before*" -o -name "*backup*" -o -name "*.old" \) \
  ! -path "./.git/*" \
  ! -path "./.venv/*" \
  | sort
    
    
  
  
List runtime directories

        
        bash
        
    
  
      find outputs uploads projects artifacts/reports cache output \
  -maxdepth 2 \
  2>/dev/null \
  | sort \
  | head -300
    
    
  
  
Check tracked noisy files

        
        bash
        
    
  
      git ls-files | grep -E '(\.bak|\.before-|^outputs/|^uploads/|^projects/|^cache/|^output/|^artifacts/reports/|^frontend/node_modules/|^frontend/dist/)'
    
    
  
  
Check untracked noisy files

        
        bash
        
    
  
      git status --short | grep -E '(\.bak|\.before-|outputs/|uploads/|projects/|cache/|output/|artifacts/reports/|frontend/node_modules/|frontend/dist/)'
    
    
  
  
Hygiene Decisions

Decision 1 — Runtime data is not source code

Runtime data should not live as first-class source files.

Applies to:

        
        text
        
    
  
      outputs/
uploads/
projects/
cache/
output/
artifacts/reports/
    
    
  
  
Future direction:

        
        text
        
    
  
      var/
    
    
  
  
Decision 2 — Backup files should not remain in active source paths

Backup files should be removed from active source paths.

Preferred source of history:

        
        text
        
    
  
      git history
    
    
  
  
If manual backups are still needed:

        
        text
        
    
  
      archive outside active source tree
    
    
  
  
Decision 3 — Frontend generated/vendor files are not part of backend audit

For Phase 6, backend audit should exclude:

        
        text
        
    
  
      frontend/node_modules/
frontend/dist/
    
    
  
  
Decision 4 — Real-estate generated reports are runtime artifacts

Files like:

        
        text
        
    
  
      real_estate_ranking_*.pdf
    
    
  
  
are generated runtime artifacts, not source assets.

Decision 5 — Example configs are allowed, local configs need review

Files like:

        
        text
        
    
  
      *.example.yaml
    
    
  
  
are acceptable.

Local active configs may remain for development, but secrets must not be
committed.

What This Step Does Not Do

This step does not:

        
        text
        
    
  
      - delete files
- move runtime directories
- change application behavior
- refactor services
- change API contracts
- change tests
- start connectors
- start packaging
- start frontend readiness
    
    
  
  
This step only documents the plan.

Next Step

After this plan is committed, the next step should be:

        
        text
        
    
  
      Phase 6 / Step 2 — Runtime Directory Standardization
    
    
  
  
That step will introduce or formalize the runtime root, update ignore rules, and
begin moving future generated state away from source paths.

Definition of Done

        
        text
        
    
  
      [x] Repository hygiene risks documented
[x] Runtime/source separation policy documented
[x] Backup file policy documented
[x] Proposed runtime root documented
[x] Proposed .gitignore additions documented
[x] Safe cleanup strategy documented
[x] No destructive cleanup performed
[x] Tests remain green
[x] Commit completed
