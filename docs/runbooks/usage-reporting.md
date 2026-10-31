# Usage Reporting

Production installations send one daily aggregate report to
`POST https://reports.gobifrost.com/usage`. Reporting is enabled by default to
estimate installation activity and Bifrost usage.

## Excluding an Installation

Development and testing environments and instances with `BIFROST_DEBUG=true`
or the `ENABLE_DEBUG` debugger switch enabled never report, even when reporting
is enabled. To exclude staging, test restores,
or other nonproduction instances configured as production, set:

```dotenv
BIFROST_USAGE_REPORTING_ENABLED=false
```

This environment setting also provides a permanent opt-out. There is no UI
toggle. In Docker Compose, set it in the deployment `.env` and recreate the API
container. In Kubernetes, set it in the ConfigMap and roll out the API deployment.
Changing the variable in a running process requires a restart.

Disable reporting before starting a restored production database in another
environment. Database copies retain the original installation identity.

## Report Contents

The report contains exactly these fields:

```json
{
  "schema_version": 1,
  "installation_id": "0a3fd3cb-9888-4c87-bc05-ce10945ba464",
  "version": "1.4.2",
  "period": "2026-10-30",
  "workflow_runs": 120,
  "agent_runs": 8,
  "environment": "production"
}
```

The installation ID is a random UUID stored in PostgreSQL's `system_configs`.
It persists across restarts, upgrades, and API replicas. It is independent of
customer accounts, domains, machine identifiers, and tenant IDs. It lets us recognize reports
from the same installation across days without receiving a customer identity.

Counts cover the previous completed UTC day. Runs are assigned to the day they
started, or their completion day when no start was recorded; queued runs that
have never started or completed are excluded. Workflow totals include retained
daily rollups so workflow run retention does not change those totals. Agent
counts use retained agent runs. An idle installation can report zero counts.

Reports exclude names, email addresses, organization IDs, domains, installation
URLs, workflow source, prompts, inputs, outputs, credentials, and financial data.
Our reporting service stores only the aggregate report and a server-generated
receipt time. It does not retain source IP addresses, user agents, referrers,
request headers, or other request metadata. Worker request logging,
observability, tracing, and Logpush are disabled.

Cloudflare processes network traffic to serve the endpoint. This promise covers
our reporting application and its configured logging; it does not claim that
Cloudflare performs no provider-level traffic processing.

## Schedule and Delivery

The existing elected scheduler checks at startup and hourly. After a successful
report, a database marker prevents further reports for that day. If delivery
fails, the next scheduled check can try again; reporting failures do not stop
workflow or agent execution. Offline days are not backfilled. The receiver
replaces a duplicate installation/day report rather than incrementing totals.

Only builds with a valid semantic version report. A release tag's leading `v`
is removed. Unversioned, debug, and test builds do not report.

Reports are unauthenticated and self-reported. They estimate installation
activity; they do not prove customer identity, unique people, or truthful usage.
