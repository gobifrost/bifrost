# Agent SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. The reviewer will
integrate after the other dispatcher stage completes.

Implement engine-local operations for `bifrost.agents.enqueue` and
`bifrost.agents.get_run`. `agents.run` composes enqueue + wait, and
`agents.wait` polls get_run, so all four entry points must avoid fixed-op
HTTP in engine children. External callers keep HTTP. Use the existing
shared `api/shared/sdk_agent_runs.py` service, also used by both HTTP
handlers. Do not duplicate its permission, paused-agent, Solution status,
usage, or steps rules in the dispatcher.

Add named ops and child methods to `api/bifrost/_local_transport.py`, wire
the SDK facade in `api/bifrost/agents.py`, and parent dispatch in
`api/src/services/execution/sdk_local_dispatch.py`. The parent must build
the actor from `LocalDispatchPrincipal`, never child fields. Preserve
HTTP DTO validation and public SDK models/errors: 404 hidden run, 409
inactive Solution, 200 paused with `AgentPausedError` facade behavior,
enqueue `run_id`, and get-run detail. Do not share a DB connection with
the child or retry failed local calls over HTTP. Enqueue's underlying
queue service owns its durable transaction; inspect it before adding any
commit. Preserve `run`/`wait` timeout and pending semantics.

Add focused unit parity tests and one real forked worker or supervised
service path with fixed agent HTTP disabled. Exercise enqueue and get_run,
including scope denial, paused and inactive Solution outcomes; test
external HTTP stays unchanged. Run `./test.sh` focused tests and
`./test.sh quality api`. Follow the repository testing protocol: diagnose
failures, no blind reruns, retries, skip, or xfail. No broad unrelated
edits and no main merge.
