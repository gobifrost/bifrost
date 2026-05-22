# Bifrost Codex Gateway

## Decision

Build the Codex Gateway as a Bifrost-native application backed by platform API
primitives in this AGPL repo. The first implementation path is not a standalone
LLM gateway and not a shared ChatGPT account proxy. It is a governance surface
for Bifrost users who connect their own ChatGPT/Codex identity.

The core attribution rule is:

```text
Bifrost user -> Bifrost gateway key/session -> same user's ChatGPT/Codex identity
```

If the same-user upstream identity cannot be resolved unambiguously, the gateway
fails closed. Service accounts are allowed only as an explicit, labeled policy
class with separate audit treatment.

## Official OpenAI/Codex Grounding

OpenAI's Codex documentation currently describes ChatGPT sign-in as a supported
Codex authentication path for subscription access, while API key sign-in remains
the usage-based path. It also states that the Codex CLI and IDE extension can use
ChatGPT sign-in, browser login, beta device-code login for headless cases, and
local cached credentials that must be treated like password-equivalent secret
material.

The Bifrost implementation should therefore avoid undocumented token exposure,
avoid logging upstream credentials, and prefer an onboarding path that either
delegates to official Codex login behavior or stores equivalent token material in
Bifrost's encrypted vault with tighter controls than local plaintext files.

## Prior Art Boundaries

The prior-art projects are useful references for compatibility behavior, but
should not be copied into Bifrost:

- LiteLLM's ChatGPT provider shows the practical shape of subscription-backed
  Codex requests, token refresh, `store=false`, streaming, and model filtering.
- CLIProxyAPI shows gateway operations, management APIs, request logs, and model
  compatibility breadth.
- ChatMock and Codex-Wrapper show small OpenAI-compatible facades and Codex CLI
  credential delegation.
- Several projects use multi-account pooling, direct private ChatGPT backend
  calls, local token files, or unclear licenses. Those are not appropriate for
  this Bifrost-native path.

Implementation borrowing must be limited to independently reimplemented behavior
and documented compatibility observations. No GPL or unclear-license code should
be copied.

## First Slice

The initial platform slice establishes reusable backend primitives:

- downstream gateway key/session context
- connected upstream ChatGPT/Codex identity metadata
- same-user upstream account resolution
- model allow/deny policy evaluation
- OpenAI-compatible structured denial shape
- audit metadata that excludes prompts and responses by default

This is intentionally below the router/UI layer. Routers, app-admin views,
token-vault persistence, `/v1/responses`, and streaming can build on this
without weakening the per-user attribution invariant.
