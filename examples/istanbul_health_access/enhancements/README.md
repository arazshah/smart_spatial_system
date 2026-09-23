# Upstream enhancement suggestions

`bugs/` is for defects — `smart_spatial_system` behaving differently from
its own documentation. This directory is for the opposite case: the
framework behaves exactly as documented, but the study still surfaced a
gap a documented, well-designed framework arguably shouldn't have — one
that would affect *any* caller with a plausible, non-expert prompt, not
just this study's specific query. Filing these as `bugs/` would misstate
what's wrong (nothing, by the framework's own contract); filing them
nowhere would lose a real, reproducible finding.

Precedent: the 0.5.0 STRtree performance fix (see `requirements.txt`'s
`0.5.0` comment block and `STUDY_LOG.md` "Current state") was reported the
same way — directly upstream, no `bugs/` file — because it wasn't a
correctness defect either. This directory formalizes that pattern for
suggestions that, unlike a one-off performance note, are worth keeping as
a standing artifact: the exact prompt sent, so a future session can tell
what was actually asked for versus what came back.

## Naming

`enhancements/NNN-short-slug.md` — same numbering discipline as `bugs/`,
independent sequence.

## Format

Looser than `bugs/`'s template, since there's no "root cause" to report —
just what's being proposed, why, and what confirms it landed:

- **Status:** proposed | accepted upstream | implemented in `<version>` | declined
- **Proposed:** YYYY-MM-DD, in response to `<what triggered it>`
- **Motivating finding(s):** link to the `paper/PLAN.md` Findings entry or entries this responds to
- **Request:** the ready-to-paste prompt actually sent to the session with write access to `smart_spatial_system`
- **Resolution:** what came back — accepted/declined/modified, the commit(s) if implemented, and confirmation method (same "clone the tag, read the real diff, don't take the fixer's word" discipline as `bugs/`)
