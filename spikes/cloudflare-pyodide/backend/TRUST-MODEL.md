# Trust model: org publishers are trusted operators, workers run least-privilege

Date: 2026-11-12. Status: accepted for the Cloudflare execution spike; proposed
for any upstream Workers backend work.

## Decision

A Bifrost org's admins, solutions writers, and workspace owners are trusted
operators — the AD/GPO analogy holds. The platform does not sandbox workflows
against their own authors. It constrains what a worker *can touch* (least
privilege) and leaves what it *costs* (CPU burn) to the operator.

## Consequences

1. **Capability discipline stays enforcing, reframed.** Declared modules,
   declared egress, no credentials in the child, gateway-injected run-scoped
   auth, output budgets, import audit — all remain hard gates, not warnings.
   Their justification is least privilege (a worker holds exactly what its
   attempt needs), not hostility. A compromised author token or a buggy
   workflow inherits the same bounds, which is the point.

2. **Non-cooperative CPU termination is NOT a ship-blocker.** If a trusted
   author's workflow burns CPU — buggy loop or otherwise — that is the
   operator's bill and the operator's timeout settings, not a platform safety
   gate. The provider's documented semantic (limit reached → throw) plus
   per-attempt CPU caps and billing alerts is the mitigation set. No kill
   primitive is assumed and none is required for workloads from trusted authors.

3. **What remains genuinely blocked (not by policy, by platform):**
   - Durable/long-lived execution (no durable sleep, 30s `waitUntil`,
     runtime-update grace periods).
   - Memory-heavy or native-dependency work (128 MiB isolate, Pyodide wheels only).
   - Trustworthy resource accounting (provider CPU ms / peak memory read null).
   - Cold-start economics for sub-second jobs (~5s fresh-isolate init).

4. **Threats this posture explicitly does NOT cover:** a malicious or
   compromised author with publish rights. That is an org trust-boundary
   issue (who gets GPO rights), identical on Linux and Workers. The gates
   above bound the blast radius; they do not replace org-level access control.

## What changed

Prior spike verdicts treated adversarial workflow authors as the gating
threat and held arbitrary workloads RED pending termination proof. That
calibration was wrong for this product: it optimized against an adversary
that rarely exists here while taxing every honest workflow with mandatory
declarations and deny-by-default friction. The declarations and gates stay —
as least-privilege enforcement, which is valuable regardless of trust — but
the RED verdict on trusted-author workloads is lifted. Termination proof is
retired as a requirement; it remains an open provider limitation, documented
as operator risk.
