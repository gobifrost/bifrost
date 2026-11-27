# Step 1 — Need: should anyone do this?

Read [Contribution Scope](../../../../CONTRIBUTING.md#contribution-scope) and use [SECURITY.md](../../../../SECURITY.md) for security reports.

## Pass when one of these is established

- **Actual use** — a concrete task someone was doing or a deployment being prepared, the
  limitation hit, and its practical consequence. Version, workaround, reproduction or
  measurement where relevant.
- **Documented interoperability** — for an existing platform surface, a primary provider source can establish a real exchange the platform cannot handle. Confirm that the proposed capability serves the documented exchange. Ask for the original sender only if its identity is needed to decide scope or compatibility.
- **Verified shipped defect** — current shipped behavior violates an established contract or
  invariant, and a bounded repair preserves intended behavior. Source evidence or a
  deterministic test can establish this. A maintainer may take up the repair whoever found
  it. Apply the public submission policy separately, including its limits on unsolicited
  automated searches.
- **Maintainer request** — a linked invitation; the item stays inside that invitation's
  scope (an invitation to measure is not an invitation to redesign).
- **Security** — credible impact or a verified gap against an applicable requirement;
  existing safeguards analysed. Exploitation not required.
- **Maintainer-originated** — the maintainer opened it or asked for it directly.

## Fail / clarify

- Unsolicited automated proposals for features, refactors, optimizations, or hypothetical
  use cases without an established need → decline. A reproduction, benchmark or finished PR does not by itself
  establish need.
- Performance claims without representative measurements → clarify (ask for numbers).
- Plausible but missing the concrete use → clarify with ONE question asking for the task
  and consequence, but only if steps 2–3 would pass; otherwise stop at the step that
  fails. Missing evidence is not proof the report is false.
- Need is about whether the problem is real. Whether the proposed shape is right
  (vendor-specific, wrong layer, wrong credential) is steps 2–3.

## Precedents

- Optimization claims require measurements before merge; verify the claim, don't infer
  it from intent (#1238).
- An arbitrary new limit presented as "hardening" needs its consumers and intent
  reviewed first; a cap that narrows existing SDK use was declined (#1198, ten-minute cap).
