# Public Form Embed Implementation Evidence

**Started:** 2026-08-04
**Branch:** `codex/form-embed-plan`
**Design source:** `docs/plans/2026-08-04-public-form-embed-plan.md`
**Current delivery state:** Implemented and delivery-QA verified.

Evidence labels follow the implementation workflow: `static-code-present`,
`current-run-automated-pass`, `current-run-browser-pass`,
`current-run-live-integration-pass`, and `not-verified`.

## Coverage matrix

`Disposition` describes the approved target. `Status` records current delivery
state so a planned replacement is never mistaken for implemented behavior.

| ID | Source surface | Requirement/journey | Disposition | Status | Implementation evidence | Data/files/policy | Automated evidence | Browser evidence | Covi check | Customer check | Notes/blocker |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F01 | `GET /api/forms/{id}` renderer load | All renderers use a sanitized runtime DTO; management retains the full DTO | replaced | verified | Runtime DTO and route; renderer service migrated | Exact-form RBAC; authoring identifiers omitted | endpoint and contract tests pass | public iframe loaded in live browser | Load authenticated and embedded forms | Embedded form loads without management metadata | — |
| F02 | Public/HMAC bootstrap | Public key and HMAC mint typed form sessions and converge on one renderer | replaced | verified | Typed bootstrap grants and shared runtime route | Public key lifecycle; encrypted HMAC secrets | token, cookie, and bootstrap tests pass | public fragment bootstrap passed | Bootstrap both grant types | Public iframe opens without login | — |
| F03 | Broad embed middleware allowlist | Exact embed-kind, method, action, and form-resource policy | replaced | verified | Deny-by-default typed middleware | Form/app separation; cookie/bearer parity | cross-form/method/action/WebSocket denial tests pass | forbidden API requests rejected | Attempt forbidden runtime actions | Public visitor cannot leave the form capability | — |
| F04 | Browser-echoed startup result | Opaque, expiring, principal/form-bound startup handle | replaced | verified | Redis-backed opaque startup handle | Bound to form/org/grant/principal; browser echo rejected | handle binding, expiry, replay, and forgery tests pass | startup-backed auto-fill passed | Load startup defaults then submit | Startup-dependent form submits correctly | — |
| F05 | Generic provider execution from forms | Field-derived options endpoint with normalized/projected results | replaced | verified | `/fields/{field}/options` resolves persisted binding | Input and metadata projection allowlists; 500-row cap | provider authorization/projection/error tests pass | provider choices and auto-fill passed | Exercise dynamic select and auto-fill | Dynamic choices populate correctly | — |
| F06 | `POST /api/forms/{id}/execute` | Unified `/submissions` service with schema validation and response union | replaced | verified | Old route removed; shared submission service installed | Exact workflow; external grants disclose no execution data | schema and authenticated/public/HMAC response tests pass | public submission and confirmation passed | Submit authenticated, public, and HMAC forms | Submission succeeds and confirmation appears | — |
| F07 | Flattened HMAC/form/startup inputs | Separate `context.form_inputs`, `context.startup`, and `context.embed` trust domains | replaced | verified | Namespaced execution context | Signed values cannot be overwritten | collision and execution-context tests pass | exercised through live submission | Submit colliding field names | Trusted integration context remains intact | — |
| F08 | Existing form upload endpoint | Session/form/file-field scoped upload and attachment validation | replaced | verified | Minted-upload registry and exact field validation | Form/session/path/MIME/size binding | wrong-field/path/session/MIME tests pass | component upload path covered; manual fixture not required | Upload and submit a file | Attached file reaches intended submission | — |
| F09 | No public publication model | Publish/review/rotate/unpublish with approved capability fingerprint | implemented | verified | Migration, ORM, review/fingerprint service, admin lifecycle endpoints | Environment-specific state; fail-closed changes | lifecycle, stale review, origin, HTML-blocker tests pass | publish and copy iframe passed | Publish, change capability, review, rotate, unpublish | Administrator controls public availability | — |
| F10 | No configurable confirmation | Portable `confirmation_markdown` with safe default | implemented | verified | ORM/DTO/CRUD/indexer/manifest/CLI/MCP mirror | Manifest/Solution round trip; publication excluded | 192 DTO/contract/manifest tests pass | authoring and rendered copy passed | Author and preview Markdown | Confirmation copy matches authored content | — |
| F11 | History navigation after every submit | Inline safe Markdown confirmation for public visitors; HMAC and authenticated history preserved | replaced | verified | Safe Markdown confirmation component and execution response branch | No raw HTML; HTTPS/same-origin images; no-referrer | renderer and sanitizer tests pass | focusable confirmation rendered publicly; HMAC reached its scoped result | Submit in all three modes | Public submit stays in iframe and thanks visitor | — |
| F12 | HMAC-only Embed Settings | Public publishing UI plus separately labeled trusted HMAC integrations | replaced | verified | Public embed administration and retained HMAC section | Admin-only mutation; explicit rotate/unpublish actions | component tests pass | admin publish/origin/copy flow passed | Review/copy/rotate/unpublish | Administrator can copy working iframe code | — |
| F13 | CSP only on bootstrap redirect | Final embedded document receives exact `frame-ancestors` policy | replaced | verified | Shared Vite/Nginx final-document policy | Strict validated origins | policy unit tests and Nginx syntax pass | allowed origin rendered; blocked origin stayed blank | Allow and deny two fixture origins | Form embeds only on configured websites in browsers | Direct clients remain possible by design |
| F14 | No public abuse boundary | Bootstrap/action rate limits, honeypot, nonce, one accepted external submission | implemented | verified | Rate limits, body cap, honeypot, nonce/idempotency | Redis counters; bounded bodies/results | rate/body/duplicate/nonce tests pass | nonce submission passed on insecure HTTP fixture | Duplicate and throttled attempts | Ordinary visitor sees usable errors | Layered abuse controls |
| F15 | Existing form DTO/manifest/CLI/MCP surfaces | Confirmation content round-trips; publication credentials never do | replaced | verified | All four surfaces updated; generated skill truth refreshed | Publication remains environment-specific | DTO parity, tripwire, and manifest round-trip pass | N/A | Export/install representative form | Form message follows form across environments | — |
| F16 | Existing audit/metrics | Publication/session/provider/submission events without sensitive values | replaced | verified | Structured security-safe event logging | No token/HMAC/field value/full-key logging | log-shape assertions and code audit pass | N/A | Inspect live logs/metrics | N/A | — |
| F17 | Existing authenticated form journey | Preserve scheduling, execution response, and history navigation | implemented | verified | Authenticated response branch retained | Existing form RBAC | scheduled execution and authenticated form tests pass | existing renderer unit journey passes | Complete authenticated submission | Signed-in user retains current workflow | — |
| F18 | Existing HMAC form journey | Preserve signing schemes/bootstrap while narrowing runtime and showing confirmation | replaced | verified | HMAC bootstrap feeds typed form grant | Shopify/HaloPSA verification; exact form only | both signing-mode and cookie-runtime tests pass | shared renderer covered; no external signer fixture | Complete both signing modes | Trusted embedded form still submits | — |
| F19 | Existing app embed journey | Preserve app workflow/execution capabilities under typed app tokens | implemented | verified | App token branch preserved independently | App scope remains distinct | app embed secret/token/middleware regression tests pass | app preview/publish tests pass in the full browser suite | Open and execute embedded app | Existing app embed remains usable | — |
| F20 | No public-form operational runbook | Document endpoints, security limits, rollout, rollback, and acceptance | implemented | verified | `docs/runbooks/public-form-embeds.md` | Migration/rollback and ownership documented | operator commands reviewed | browser acceptance procedure executed | Follow operator checklist | Follow business acceptance checklist | — |
| F21 | No anonymous proof-of-work | Self-hosted ALTCHA proof gates public submissions and remains optional per publication | implemented | verified | Session-bound challenge route, WASM solver, signed proof verification, and Share-dialog toggle | HKDF-derived signing key; five-minute expiry; exact form/session binding; atomic Redis redemption | derivation, expiry, tamper, cross-session, replay, route-policy, HMAC-bypass, and component tests pass | real proof solved and submitted from a plain-HTTP iframe; HMAC rendered no CAPTCHA | Enable, disable, solve, retry, and inspect errors | Public visitor can verify without a vendor account | Proof-of-work raises automation cost; it is not client authentication |
| F22 | Share dialog and authenticated Launch polish | Keep confirmation authoring stable across a visually faithful Preview, expose presentation options before the generated embed, place lifecycle actions by their artifacts, and remove first-use runner delay | implemented | verified | Controlled, force-mounted confirmation tabs; shared editor/rendered-Markdown typography; `Update` copy; live Theme/Header/Transparent snippet controls; right-aligned Rotate beneath embed; form-runner route preload | No authorization or data-policy change | focused client tests, TypeScript, and ESLint pass | real TipTap edit/visually formatted preview, embed-option rewrite, and public iframe submission pass in Dockerized Playwright | Configure/copy the embed, edit/preview confirmation, then Launch from list | Copied code reflects the chosen presentation; Preview reflects the formatted draft; Launch does not wait for the route chunk | Browser assertion compares computed heading/body font sizes; live runtime API measured at 14 ms |

## Current evidence

- `current-run-automated-pass`: API pyright and ruff pass with zero findings;
  TypeScript and ESLint pass; generated client types and skill truth are
  current. The final repository-wide backend run passed 7,207 tests and skipped
  57 environment-dependent cases. The final client run passed all 237 files and
  1,693 tests with concurrency bounded to two workers because the shared
  eight-core host was above a load average of 100; the same test deadlines were
  retained. Contract, DTO parity, generated-reference, and mirror tripwires
  passed 180/180 with two expected environment skips. The focused security
  coverage includes cryptographic derivation, expiry, tamper,
  cross-form/cross-session reuse, atomic replay rejection, route policy,
  public/HMAC response behavior, and contract versioning.
- `current-run-browser-pass`: the public journey publishes a real workflow- and
  provider-backed form through the admin UI, loads it from a second allowed
  Docker origin, queries options, applies auto-fill, submits, renders the custom
  confirmation without execution disclosure, and verifies a second blocked
  origin is denied by final-document CSP. The final complete Playwright run
  passed 110 scenarios and skipped two fixture-dependent OAuth cases without
  retries. It includes a real ALTCHA proof on plain HTTP, an HMAC submission
  that rendered no CAPTCHA and reached its execution result, computed-style
  confirmation-preview verification, and the existing authenticated form and
  app-embed regressions. The final confirmation screenshot was reviewed.
- `current-run-live-integration-pass`: the browser test uses the complete
  Dockerized API, worker, client, database, Redis, RabbitMQ, and object-storage
  stack rather than mocked HTTP responses. The authenticated runtime endpoint
  was also timed directly on the live development stack at 14 ms, isolating the
  observed Launch pause to the lazy client route; the Forms page now preloads
  that route while the list is visible.

## Gate status

- Implemented gate: **passed** — all planned rows have concrete implementation
  and focused automated evidence.
- Integration-tested gate: **passed** — the complete public form journey and
  security boundary run against live services.
- Delivery-QA gate: **passed** — focused and repository-wide browser acceptance,
  static checks, security tests, contract checks, and screenshot review are
  complete.
- Customer acceptance: **accepted for merge** — the product owner exercised the
  development UI and embedded form, approved the final behavior, and explicitly
  authorized merge on 2026-08-05. Production deployment remains a separate
  release action.
