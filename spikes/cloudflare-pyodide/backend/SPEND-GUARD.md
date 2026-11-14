# Spend guard: what Cloudflare can and cannot cap

Verified 2026-11-13 against `cf` 1.0.0-beta.31 and the current Cloudflare docs
(`developers.cloudflare.com/billing/manage/spend-management/`). This is the
operational guard for the Cloudflare backend, and the honest limits of it.

## What exists

A **spend policy** is one rule per product per account:

| Field | Values |
|---|---|
| `product_id` | `workers`, `durable_objects` |
| `action` | `notification` (email) or `usage_block` (stops usage) |
| `threshold_cents` | positive integer |
| `enabled` | boolean |

- One enabled `usage_block` per product per account; unlimited `notification`
  policies.
- A `workers` policy covers **requests and CPU time across all Workers in the
  account**, after included usage. It excludes the subscription fee.
- At the threshold, Cloudflare rejects **new invocations** (HTTP `1103`) for
  every Worker in the account until the limit is raised, disabled, deleted, or
  the billing period rolls over.
- It is not an exact maximum bill: reporting and blocking lag, so usage can
  continue past the threshold.

## What it cannot cap

**Dynamic Worker creation** — `$0.002` per worker per day — is a separate
Dynamic Workers line item, and `product_id` accepts only `workers` or
`durable_objects`. There is no policy that caps creation.

That matters because creation is the dominant cost of the per-attempt posture:
10,000 runs/month is ≈$18 of creation versus ≈$0.35 of CPU. A Workers spend
policy caps the cheap line and not the expensive one.

It also cannot scope to a tenant, a solution, or an environment. The docs say it
plainly: a test Worker can reach the shared limit and interrupt a production
Worker in the same account.

## What the guard does

`dev/tenant-workers/spend-guard.mjs` wraps the CLI:

```sh
node spend-guard.mjs list
node spend-guard.mjs plan --tenants 35 --runs 500 --days 30
node spend-guard.mjs apply --action notification --threshold-cents 2500 \
  --email ops@example.com --name "Bifrost spike guard"
node spend-guard.mjs delete <POLICY_ID>
```

`apply --action usage_block` is **refused without `--confirm-block`**, because it
is account-wide and can take production down. That is a deliberate gate, not a
missing feature.

Measured `plan` output (35 tenants, 500 runs/month, 30 days):

```
  requests   500               $0.00   (cap-able)
  CPU        0.88M CPU-ms      $0.00   (cap-able)
  creations  1,050 workers-day $0.10   (NOT cap-able)
  Workers spend policy covers: $0.00
  Outside the policy:          $0.10
```

## Current account state (2026-11-13)

Metered with `cf billing usage get-v1` for the current period (starting
2026-10-30), received through `results/cloud-billing-usage.json`:

| Meter | Consumed | Included | Billed |
|---|---|---|---|
| Workers Standard Requests | 904,321 | 10M | $0.00 |
| Workers CPU ms | 1,666,986 | 30M | $0.00 |
| **Dynamic Workers** | **105** | **1,000** | $0.00 |

That reads correctly against the architecture: requests and CPU are at ~9 % and
~5.6 % of allowance, while Dynamic Workers is already at **10.5 %** of the
monthly included 1,000 — from a spike that deployed, ran acceptance suites, and
metered a handful of tenants. In the per-attempt posture the same traffic would
consume one creation per invocation instead of one per tenant per day.

Policy created (notification only, nothing blocks):

| Field | Value |
|---|---|
| id | `3b06d25b-e0a3-4a8e-94f1-0745c924eeab` |
| product | `workers` |
| action | `notification` |
| threshold | $25.00 |
| email | account owner |
| enabled | true, version 1 |

`cf billing spend-policies list` → one policy. No `usage_block` exists; that
remains behind `--confirm-block` and the account-wide blast radius above.

Writing policies needed a token with **Account → Billing → Edit**; the normal
OAuth login has Account & Billing `14/15` scopes and returns 403 on write and on
`billable-usage`, but can read the policy list. Usage readback (`get-v1`) also
required Billing Read.

## Where per-tenant cost control actually lives

Cloudflare cannot express a per-tenant cap, so the dispatcher must:

1. **Use stable per-tenant worker ids** — creation then costs one per tenant per
   day, and zero on reuse (measured: +0 creations for 3 invocations).
2. **Enforce a per-tenant run/CPU budget before dispatch**, failing closed to
   Linux rather than letting a tenant overspend.
3. **Rotate a tenant's id as a quarantine action**, not as a deployment step.
4. Treat the Cloudflare policy as a **backstop for the cap-able line only**, and
   keep the account-wide `usage_block` behind explicit human approval.

## Provenance

- Tool: [`dev/tenant-workers/spend-guard.mjs`](../dev/tenant-workers/spend-guard.mjs)
- Dry-run body (validated): `POST /accounts/<id>/billing/spend-policies` with
  `{action, enabled, name, notification_email, product_id, threshold_cents}`.
- 403 receipt for write and for `billable-usage` on the current login.
