# CI image publication and operator deployment schedules

Bifrost CI builds, tests, signs, attests, and publishes GitHub Container Registry
images. The operator's deployment repository controls image selection, rollout
timing, and deployment health. CI does not connect to the production Kubernetes
cluster or wait for each published image to appear there.

The former `deploy-dev` job originally restarted workloads using a floating
`:dev` tag. It later became an observer of immutable-tag image automation. Its
immediate rollout expectation conflicts with operator-controlled schedules and
is removed along with its manual DigitalOcean authentication dry run. Image
publication and merge requirements retain their existing gates.

Operators can promote images continuously, on a schedule, or manually using
their deployment tooling. Kubernetes readiness/liveness probes continue to run.
The removed GitHub job's post-rollout API/client smoke checks and workflow-failure
alerts are no longer provided by CI; any desired post-rollout verification
belongs with the operator's deployment process.

No deployment-cadence repository variable is required.
