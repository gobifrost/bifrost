# CI deployment verification and operator schedules

The `deploy-dev` CI job observes the external image updater after a main-branch
image publication. It does not initiate a Kubernetes rollout.

By default, or when `BIFROST_VERIFY_IMMEDIATE_DEPLOYMENT` is any value other than
`false`, CI waits for the published version to reach DigitalOcean and checks
rollout status and application readiness. Operators who promote images on a
schedule can set the GitHub repository variable
`BIFROST_VERIFY_IMMEDIATE_DEPLOYMENT=false` to skip that immediate observer.
This preserves the merge gates, image publication, signing, and attestations.

Merge this support before changing the repository variable and enabling the
deployment schedule. The operator's scheduled/manual promotion procedure must
check deployment readiness; skipping the immediate observer does not add a
scheduled CI smoke check.

To restore immediate verification, first restore continuous image promotion,
then remove the variable or set it to `true`.
