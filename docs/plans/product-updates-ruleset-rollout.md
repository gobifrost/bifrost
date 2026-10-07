# Product Updates Ruleset Rollout

This is a reviewed rollout artifact only. It does not call the GitHub API or
change live repository settings.

## Frozen before state

The captured live state is preserved in
[`product-updates-ruleset-before.json`](product-updates-ruleset-before.json).
It identifies repository ruleset `15329014`, **Protect Branch**, with no bypass
actors and these required checks:

- `Lint & Type Check`
- `Unit Tests`
- `E2E Tests`

The remaining rules are deletion prevention, non-fast-forward prevention, the
existing pull-request policy, and the existing squash merge queue. They are
out of scope for this rollout.

## Proposed delta

[`product-updates-ruleset-proposed.json`](product-updates-ruleset-proposed.json)
is the complete reviewed after state. Apply the single JSON Patch in
[`product-updates-ruleset-rollout.patch.json`](product-updates-ruleset-rollout.patch.json)
to a freshly fetched copy of ruleset `15329014`; its result must equal the
proposed snapshot apart from GitHub's volatile fields. The resulting
`required_status_checks` list, in order, is:

1. `Lint & Type Check`
2. `Unit Tests`
3. `E2E Tests`
4. `Product Updates`

No other field, rule, bypass actor, merge method, queue setting, or enforcement
state may change. A reviewer must compare the fresh before/after JSON after
removing only volatile fields (`updated_at`, links, and node id); any remaining
difference outside that additional context stops the rollout.

## Readiness gate

Apply this delta only after the `Product Updates` workflow has reported green
for a normal source PR, a content-only PR, a PR metadata edit, and a
multi-PR merge-group candidate. The workflow validates untrusted PR content
with a base-commit validator and read-only permissions; it resolves every PR
in the merge group before accepting its canonical disposition.

This branch has not applied the delta. Live ruleset changes require separate
authorization after that evidence is reviewed.
