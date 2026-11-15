# Cloudflare onboarding and native-scale receipts

This report records two bounded, private qualification attempts. It is not a
Cloudflare conformance or production qualification. The sanitized companion is
[the onboarding and scale baseline](baselines/cloudflare-onboarding-scale-2026-11-15.json).

## Phase seven: settings onboarding succeeded; fresh workflow did not qualify

The corrected phase-seven API and worker image were built from
`1c7f2bba3`. The settings onboarding job
`f9a7e044-54f5-4ac9-a42c-181e5dc6e8d2` completed successfully against
`https://bifrost-d1b5ca3f-oglx.eu1.netbird.services`.

| Receipt | Value |
|---|---|
| Worker image | `registry.cloudflare.com/<account>/bifrost-spike-1400-p7@sha256:94466c72c3eea2adf2ed776d8e491076298974ae8c366659b18b656bde56d75e` |
| Provider-deployment image | `registry.cloudflare.com/<account>/bifrost-spike-1400-p7@sha256:2988f648a5325ef39f4425c3e5796cb9db81e2cfa1fe7e84ec258bf770aed87b` |
| Settings screenshot | `/tmp/bifrost-1400-spike/cloudflare-phase7/screenshots/onboarding-desktop.png` |
| Fresh workflow | `367d596a-59fe-44aa-b1be-f271623ae417` |

The fresh workflow stayed `Pending` for the full 240-second observation window.
The capacity row remained `starting`; the read-only hook recorded zero
`ContainerStarted` events and zero running containers. The receipt then
cancelled the workflow, with a warm-start count of zero. Consequently, phase
seven did **not** qualify a fresh end-to-end workflow launch, despite the
successful settings onboarding job.

## Phase six: native work succeeded on a different execution

The native qualification used the API and worker image at `304c0d130`. Its
provider repair was applied manually from `1c7f2bba3`; that is a material
configuration and source differential, not evidence that phase seven's
settings-created provider can launch work.

Observed native successes were bounded to:

- an application deployment on an external worker;
- completed native agent work;
- a completed parent/child delegation and their summaries; and
- a corrected five-summary backfill, with two physical Cloudflare containers
  recorded by the read-only hook and claims on two distinct external workers.

The corrected summary receipt reports five successes, no failures, and
`$0.01108000` provider cost. It proves that this bounded corrected backfill
used two physical workers; it does not prove general workflow scale or current
onboarding behavior.

An earlier 256-token profile run completed its agent response but failed summary
parsing and recorded `$0.00391600`. The prior scale probe started two physical
containers but all five children failed before model inference after an accepted
backfill read returned HTTP 404; its provider-reported cost was `$0.0000` and it
did not prove two-worker execution. The receipt set for other native agent and
delegation activity is not complete enough to calculate an aggregate cost, so
this report intentionally provides no aggregate or invoice attribution.

## Claims deliberately not made

- Current Cloudflare capacity conformance or a passing 17-case suite.
- Fresh settings-onboarding workflow qualification.
- General Cloudflare workflow scale, recovery, or singleton guarantees.
- Aggregate native-provider cost, invoice attribution, or production readiness.
