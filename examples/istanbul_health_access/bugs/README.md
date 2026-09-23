# Upstream bug reports

`smart_spatial_system` is a **pinned dependency** of this repository and its
source is never edited from here (see `STUDY_LOG.md`). When something in it
behaves incorrectly while running an experiment, the deliverable is a report
in this directory — then **stop and surface it**, rather than working around
it and carrying on with a result nobody can reproduce.

## Naming

`bugs/NNN-short-slug.md` — e.g. `001-crs-mismatch-silent-nonsense-distance.md`.
Numbers are sequential within this repo and are *not* upstream bug numbers.

## Format

One file per bug, in the shape `smart_spatial_system`'s own `CHANGELOG.md`
uses for its bug entries: **root cause**, **reproduction**, and a proposed
**two-part fix (plugin layer + LLM-prompt layer)**.

> **Checked against the real `CHANGELOG.md` (2026-09-19, `v0.3.0` tag).**
> Upstream does **not** use literal `Root cause:`/`Reproduction:` headers —
> it's plain [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) prose
> under `### Added`/`### Changed`/`### Fixed`. But the *substance* the brief
> asked for is genuinely there in every LLM-related fix: e.g. `[0.2.2]`
> narrates the exact mechanism (plans fanning distance calculations out from
> the original vector instead of chaining them, so scores silently landed on
> the wrong, unrelated field), then pairs **a plugin-layer fix**
> (`LLMQuerySpecGenerator.generate()` now validates every `score_features`
> factor is reachable through its op's own input chain, raising
> `LLMSpecGenerationError` by name instead of silently scoring 0) **with an
> LLM-prompt-layer fix** (`_domain_guidance()` gained a worked wrong-vs-right
> example). That pairing — a validator that turns the failure loud, plus
> prompt guidance so the model stops generating it — is what this
> repository's explicit-header template below makes scannable for a student
> bug tracker; it is a structured rendering of upstream's habit, not a
> literal copy of its prose format. Keep the headers; don't claim they're
> copied verbatim from upstream.

The two-part fix is the part that is easy to shortchange and the part that
matters most. Most failures in this family have both a **deterministic**
face (the framework accepted something it could have rejected, or silently
produced nonsense instead of raising) and a **generative** face (the LLM was
never taught not to produce it). Fixing only the prompt leaves the trap open
for the next model; fixing only the validator turns a silent wrong answer
into a loud failure without making the planner any more likely to succeed.
Propose both, every time, and say so explicitly if one half genuinely does
not apply.

```markdown
# NNN — <one-line symptom, as a reader would recognise it>

- **Status:** open | reported upstream | fixed in <version>
- **Found:** YYYY-MM-DD, phase <n> (<notebook>)
- **Affects:** smart-spatial-system <pinned version>
- **Severity:** <silent wrong answer | hard failure | misleading plan>

## Symptom

What was observed, in terms of the experiment's output. If it is a *silent*
wrong answer, say so in the first sentence — those are the expensive ones.

## Root cause

The actual mechanism, located in upstream source by file and function (read
it; do not infer it from behaviour alone). Name the file and symbol, e.g.
`plugins/nearest_neighbor.py::run`, and quote the few lines that matter.

## Reproduction

A minimal, self-contained path from a clean install of the pinned version to
the wrong behaviour: the input, the spec/plan, the command, the observed
output, and the expected output. If it needs this repo's data, name the
exact committed file under `data/processed/` so it stays reproducible.

## Proposed fix

### Plugin layer (deterministic)

What the framework should do so the failure cannot be silent — a validation
check that raises, a precondition assert, a corrected computation. Name the
function that should change.

### LLM-prompt layer (generative)

What the planner's guidance should say so a model stops generating the
faulty plan in the first place — the concrete worked example or explicit
requirement to add, and where (e.g. `_domain_guidance()`).

## Local mitigation in this repo, if any

Prompt-side `system_hints` or post-processing added *here* as a named safety
net until the pin moves. Say exactly where it lives and what removes it.
Never an undocumented workaround — if it is not written down here, it does
not exist.

## Resolution

Upstream version that fixes it, and the commit here that bumps the pin and
re-runs the affected phases.
```

## Candidates inherited from the Vienna study

Not bugs in *this* repo's experience — nothing has been run here yet — but
the failure modes most likely to reappear, worth recognising fast. All three
were silent wrong answers, not crashes:

1. **Un-chained operations.** `score_features` wired to a pre-distance
   vector, so every score is `0.0` while the run reports success. Fixed
   upstream in 0.2.2.
2. **`score_features` normalisation overwriting the domain.**
   `normalize_llm_query_spec_for_planning()` substituting a hardcoded
   *real-estate* scoring spec when one is incomplete, regardless of domain.
3. **CRS mismatch.** A plan reprojecting the sites layer but not the layers
   it measures distance to; the distance op returns a nearly-constant
   nonsense value instead of raising. This is the one this repo is most
   exposed to — two layers, one distance call, a 2000 m threshold that will
   look plausible either way.
