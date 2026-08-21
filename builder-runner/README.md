# Bifrost Build runner

`ghcr.io/gobifrost/bifrost-build` is Bifrost's signed canonical image for the
optional Cloudflare execution runtime. During setup, Bifrost uses short-lived
Cloudflare registry credentials to mirror that public image into the hoster's
own `registry.cloudflare.com` namespace; the hoster does not configure another
registry or run Docker. The image executes native Solution Builder turns and
canonical application builds.

It accepts one versioned job envelope and one short-lived capability scoped to
that exact `PlatformJob` attempt. The container receives no Cloudflare or user
credentials. For Builder turns, Bifrost sends the already-configured AI model
and its decrypted provider key over TLS to the fenced attempt; the key remains
in memory for that job and is never stored in the workspace or returned in
events.

The image runs the same Python components as the existing Worker:

- `AgentRuntimeRunner` for the Pydantic AI loop, compaction, request limits, and
  token limits;
- the shared message-history normalizer and Chat V3 stream contract;
- the same bounded Builder filesystem tools;
- `run_local_app_build` for npm/Vite compilation.

Bifrost remains authoritative for conversation/tool persistence, attachments,
generated artifacts, AI usage, progress, cancellation, immutable revisions,
and deployment. Cloudflare only supplies isolated compute.

The Cloudflare Worker has no public HTTP route. Bifrost starts a Workflow via
the Cloudflare API; that Workflow writes the envelope into a Sandbox, invokes:

```text
bifrost-sandbox-runner /work/bifrost-job.json
```

and destroys the Sandbox after the terminal callback.
