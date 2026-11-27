# Verdict format

Keep the verdict readable on a phone. Report completed checks and omit fields that do not apply.

```
**#<N or thread title> — <descriptive title>** · <bug|feature|behavior-change|security|maintenance>
**Verdict:** keep | revise | decline | defer | clarify | answer | duplicate | already-resolved
**Decided at:** step <0 Current State | 1 Need | 2 Fit | 3 Design | 4 UI | 5 Verify> (or "all applicable steps pass")
**Fast path:** yes | no
**Why:** 2–4 sentences: the decisive evidence and reasoning.
**Steps:** Need: pass/fail/clarify — one line · Fit: … · Design: standards …, tenancy …, compatibility …
**Placement:** Solution | shared capability | native tool | existing path repair
**Reuse:** what existing feature/component/path this builds on (or "none found", with what you searched)
**Question:** (clarify only) the one question the requester must answer
**Next step:** smallest coherent next action and suggested owner
```

## Verdicts

- **keep** — all steps pass; build it as stated.
- **revise** — the same item can pass with a bounded rework that preserves its purpose and ownership. State the required change.
- **decline** — the submitted item should stop. If the viable approach is a separate capability, belongs in another layer or has a different owner, name that smallest separate item without converting the current submission into it.
- **defer** — valid but not now; name the trigger that reopens it and a revisit date.
- **clarify** — one question whose answer could change the verdict.
- **answer** — explain supported behavior and any practical workaround. When the question exposes an interoperability gap covered by a standard, also propose the smallest standards-based addition to the shared platform path. Keep that recommendation distinct from a roadmap commitment.
- **duplicate** / **already-resolved** — from step 0; link the canonical item or the
  commit/PR on `origin/main` that resolved it.

Multi-ask items: one verdict and deciding step per ask. For a verified defect outside public contribution scope, state the maintainer recommendation and the submission disposition separately. RFCs: replace Verdict with
`**Open decisions:**`, one line each, with your recommendation.

## Side findings

Handle an unrelated security finding separately through the private route in [SECURITY.md](../../../../SECURITY.md). Record private reporting only after it occurs.
