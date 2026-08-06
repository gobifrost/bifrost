# Unified Form Runtime, Public Embeds, and Markdown Confirmation

**Created:** 2026-08-04
**Status:** Implemented and verified
**Owner:** Platform
**Scope:** Forms runtime, public publishing, HMAC form embeds, confirmation UX,
security boundaries, and end-to-end verification.

## Problem and user need

Bifrost forms already support authenticated execution and HMAC-authenticated
iframe embedding, but the current surface is an execution-oriented integration
rather than a public form product:

- the iframe sample requires an external system to compute an HMAC;
- form fields invoke data providers through the generic
  `POST /api/workflows/execute` endpoint;
- form submission returns a `WorkflowExecutionResponse` and the client navigates
  to execution history;
- all embed principals share one broad middleware allowlist, including generic
  workflow execution and execution reads;
- the embed router mints ordinary `type="access"` tokens while a separate embed
  token helper/tests assume `type="embed"`, leaving overlapping token contracts
  to reconcile;
- startup results are returned to the browser and accepted back from the browser
  during submission;
- submitted form data is not validated against the server-side form schema;
- HMAC-verified query parameters are merged into the same namespace as form
  input, and form input currently wins on collision.

The desired product is a Formstack-style iframe that an administrator can copy
and paste into a website. A public or HMAC form session may load one form, run
that form's startup workflow, query only data providers assigned to fields on
that form, upload files for that form, and submit only that form's linked
workflow. It may not inspect workflow output or execution history. After a
successful submission, it displays the form's customizable Markdown
confirmation in place.

## Decisions locked by this plan

1. **One forms runtime.** Authenticated, public, and HMAC sessions use the same
   `/api/forms/{form_id}/*` runtime endpoints. `public` and `hmac` describe how
   authority was obtained, not separate resource namespaces.
2. **Separate bootstrap paths.** A public key and an HMAC signature remain
   different ways to mint a form-session token. After minting, both converge on
   the same form endpoints and frontend renderer.
3. **Move, do not duplicate, form execution.** Replace
   `POST /api/forms/{form_id}/execute` with
   `POST /api/forms/{form_id}/submissions`. Do not retain a permanent alias.
4. **Generic workflow execution remains generic.** Apps, administrators, and
   other non-form clients retain `POST /api/workflows/execute`; form sessions
   cannot call it.
5. **Provider authorization is field-derived.** The caller names a form field;
   the server loads that field's configured `data_provider_id`. The caller never
   supplies an arbitrary workflow/provider ID.
6. **Public publishing is approval.** Publishing a form explicitly approves the
   submission workflow, startup workflow, provider-backed fields, provider
   metadata exposed through auto-fill, and file fields listed in the publish
   review. The approved capability fingerprint is enforced on bootstrap and
   every public runtime call; a capability-affecting edit pauses public access
   until it is reviewed and republished. There is no separate `public_safe`
   checkbox in v1.
7. **Public sessions receive no execution access.** HMAC sessions may receive
   and read only the exact execution created by that session; they cannot list
   history or access another execution. Bifrost retains the internal execution
   relationship for authorized operators.
8. **Inline Markdown confirmation only in v1.** No configurable redirect. The
   default is:

   ```markdown
   ## Form submitted

   Thank you!
   ```

9. **External images are supported through ordinary Markdown image syntax.**
   Raw HTML and executable content are not supported.
10. **Authenticated and HMAC result behavior remains compatible.** Logged-in
    users and trusted HMAC sessions receive the execution summary and navigate
    to their authorized result. Public sessions receive the confirmation-only
    response. All paths still share the same submission service.
11. **HMAC context is trusted but separate.** Signed values live in
    `context.embed`; they are never merged into user-editable `form_data`.
12. **A public embed is not bot-proof authentication.** Origin controls govern
    browser framing. Direct HTTP clients can still reproduce public requests.
    Self-hosted proof-of-work raises the cost of automation but can also be
    implemented by a direct client. Server-side scope, validation, throttling,
    and one-time submission semantics contain the remaining risk.
13. **Runtime definitions are deliberately smaller than management records.**
    Every renderer loads `GET /api/forms/{form_id}/runtime`, which omits workflow
    IDs, provider IDs, organization/access metadata, roles, HMAC settings, and
    other authoring-only data. `GET /api/forms/{form_id}` remains the authenticated
    management representation.
14. **Executable HTML is not publishable in v1.** A public publication review
    blocks forms containing `html` display fields, including JSX templates.
    Markdown display fields remain supported. Authenticated and existing HMAC
    forms retain their current HTML-field behavior; the confirmation renderer
    never accepts HTML.

## Goals

- Produce a copyable iframe with no customer-held secret.
- Make every form-session capability exact-form and exact-action scoped.
- Route every form runtime operation through `/api/forms/{form_id}/*`.
- Preserve the existing HMAC integration modes while narrowing their runtime
  authority.
- Render an accessible, customizable Markdown confirmation after submission.
- Make public submission safe against cross-form, cross-org, provider
  substitution, workflow substitution, startup forgery, arbitrary parameter
  injection, and execution-data disclosure.
- Verify the complete experience against a live form embedded on a second web
  origin.
- Gate anonymous submissions with optional, default-on, self-hosted proof-of-
  work that requires no external CAPTCHA account.

## Non-goals

- Proving that an anonymous request came from a human browser rather than curl,
  PowerShell, or a proxy.
- Password-protected forms, SSO forms, or per-recipient invitation links.
- A hosted top-level public form URL. V1 is an iframe experience.
- Redirect confirmations, conditional confirmations, or workflow-selected
  confirmation content.
- Raw HTML in confirmation content.
- Third-party or provider-specific CAPTCHA integration. V1 uses self-hosted
  ALTCHA proof-of-work with no external account or service.
- Published immutable form revisions. V1 serves the live active definition.
  Copy, layout, ordinary field, and confirmation changes update immediately;
  workflow, provider, auto-fill exposure, file capability, or executable-content
  changes require a new publication review.

## Current and target request flows

### Current authenticated flow

```text
/execute/{form_id}
  -> GET  /api/forms/{form_id}
  -> POST /api/forms/{form_id}/startup               (optional)
  -> POST /api/workflows/execute                      (provider ID from browser)
  -> POST /api/forms/{form_id}/execute
  <- WorkflowExecutionResponse with execution_id
  -> /history/{execution_id}
```

### Current HMAC flow

```text
/embed/forms/{form_id}?...&hmac=...
  -> validate a per-form shared secret
  -> mint an 8-hour embed access token
  -> redirect /execute/{form_id}#embed_token=...
  -> use the same runtime calls as the authenticated flow
```

### Target converged flow

```text
Authenticated entry                Public entry
/execute/{form_id}                 /embed/forms/public/{public_key}
  user access token                  -> resolve active publication
                                     -> mint public form-session token

HMAC entry
/embed/forms/{form_id}?...&hmac=...
  -> validate HMAC
  -> mint HMAC form-session token + context.embed

All three renderers then use:
  -> GET  /api/forms/{form_id}/runtime
  -> POST /api/forms/{form_id}/startup
  -> POST /api/forms/{form_id}/fields/{field_name}/options
  -> POST /api/forms/{form_id}/upload
  -> POST /api/forms/{form_id}/submissions

Authenticated/HMAC response: execution summary -> authorized result UX
Public response: confirmation Markdown -> inline success screen
```

The frontend does not select a different runtime endpoint based on entry mode.
The API client attaches the active token. The server derives the authorization
policy from the principal and token claims.

## API design

### Runtime endpoint migration

| Operation | Old | New | Notes |
|---|---|---|---|
| Manage form | `GET /api/forms/{id}` | unchanged | Authenticated authoring/RBAC only |
| Load renderer | `GET /api/forms/{id}` | `GET /api/forms/{id}/runtime` | Exact binding; sanitized runtime projection |
| Startup | `POST /api/forms/{id}/startup` | unchanged | Store trusted result server-side for sessions |
| Provider options | `POST /api/workflows/execute` | `POST /api/forms/{id}/fields/{field_name}/options` | Field-derived provider |
| Submit | `POST /api/forms/{id}/execute` | `POST /api/forms/{id}/submissions` | Shared service; remove old route |
| Upload | `POST /api/forms/{id}/upload` | unchanged | Exact form/session upload prefix |
| Execution read | `/api/executions/*`, `/ws` | unavailable to form sessions | Authenticated users unchanged |

### Runtime definition

Add `FormRuntimeDefinition` as the only form-definition response consumed by
`RunForm`/`FormRenderer`. It contains the renderable title, description,
portable field schema, confirmation-independent presentation settings, and
booleans such as `has_startup` and `has_dynamic_options`. A provider-backed field
may expose the configured input mapping needed by the renderer, but never the
provider/workflow UUID.

It must not serialize submission/startup workflow IDs or refs, provider IDs,
organization IDs, access level, roles, creator/audit metadata, HMAC settings,
publication keys, or dependency-review details. The server owns all of those
bindings. This is a runtime projection, not a second implementation of form
behavior: authenticated, public, and HMAC renderers all use it, while the
existing full form DTO remains for authenticated management screens.

### Publication management

Authenticated platform-admin endpoints:

```text
GET   /api/forms/{form_id}/publication
GET   /api/forms/{form_id}/publication-review
PUT   /api/forms/{form_id}/publication
DELETE /api/forms/{form_id}/publication
POST  /api/forms/{form_id}/publication/rotate-key
```

`publication-review` returns the exact dependency set being exposed and a
fingerprint:

```json
{
  "fingerprint": "sha256:...",
  "submission_workflow": {"ref": "...", "name": "..."},
  "startup_workflow": {"ref": "...", "name": "..."},
  "provider_fields": [
    {
      "field_name": "customer",
      "provider_ref": "...",
      "provider_name": "Customer options",
      "metadata_targets": ["customer_name", "customer_email"]
    }
  ],
  "file_fields": ["attachment"],
  "warnings": [],
  "blockers": []
}
```

Publishing requires the reviewed fingerprint. The server recomputes it and
returns `409` if the form changed between review and confirmation. This makes
the publish action meaningful approval rather than a stale UI warning.
Publication review lists any `html`/JSX display fields as blockers, and the
server refuses to publish while blockers exist. The UI explains that Markdown
is the supported public display-content format in v1; this check is enforced by
the publication service, not only by the browser.

The stored fingerprint covers the submission/startup workflow bindings,
provider field bindings and configured inputs, auto-fill metadata projection,
file fields and constraints, and the presence of blocked executable content. A
public bootstrap and every public runtime action recompute/compare it. A
mismatch returns the generic unavailable response and marks the publication as
`needs_review`; no newly selected dependency runs under an older approval.
Cosmetic and confirmation content are deliberately excluded from this security
fingerprint so safe authoring changes remain live.

`DELETE` unpublishes and revokes public bootstrap immediately. It does not
delete the form or HMAC secrets. `rotate-key` invalidates the old iframe URL and
returns a new public key.

### Public bootstrap

```text
GET /embed/forms/public/{public_key}
```

- Resolve an active `FormPublication` by opaque key.
- Require the form and linked Solution installation to be active.
- Apply public-session bootstrap rate limiting.
- Mint a 30-minute, non-refreshable form-session JWT.
- Redirect to `/embedded/forms/public/{public_key}#embed_token=...`. The token is
  in the URL fragment so it is not sent through request logs or referrers; the
  already-public key remains in the path so the web tier can resolve frame
  policy before serving the final document.
- Never disclose whether a random key once existed; inactive and unknown keys
  both return the same `404` response.

### HMAC bootstrap

Keep:

```text
GET /embed/forms/{form_id}?...&hmac=...
```

Refactor it to call the same form-session token factory as public bootstrap,
with `grant="hmac"` and the verified query values stored under
`verified_context`. Preserve the current Shopify and HaloPSA signing schemes and
the existing eight-hour token lifetime for integration compatibility.
Redirect successful HMAC bootstrap to
`/embedded/forms/hmac/{form_id}#embed_token=...`; the HMAC and verified query
values must not survive in the final path or query string.

HMAC embedding does not require a public publication. Deactivating the matching
secret still prevents new HMAC sessions. HMAC and public sessions otherwise
receive the same runtime action set and neither receives execution access.

### Form-session claims

```json
{
  "type": "access",
  "embed": true,
  "embed_kind": "form",
  "grant": "public",
  "sub": "<system-user-id>",
  "form_id": "<uuid>",
  "org_id": "<uuid-or-null>",
  "jti": "<uuid>",
  "capability_fingerprint": "sha256:...",
  "is_external": true,
  "exp": "<grant-specific-expiry>"
}
```

HMAC sessions additionally carry `verified_context`. App embed tokens retain
their existing `embed_kind="app"` behavior. Do not infer form/app policy solely
from a shared `embed=true` flag after this change.

Public runtime authorization also requires an active publication whose current
capability fingerprint matches the token claim. Unpublishing therefore revokes
already-minted public sessions immediately. Rotating only the public key blocks
the old bootstrap URL; a session minted before rotation remains scoped and valid
until its short expiry. HMAC grants are independent of publication state.

### Submission contracts

Rename `FormExecuteRequest` to `FormSubmissionRequest`:

```json
{
  "form_data": {},
  "startup_handle": "<opaque-handle-or-null>",
  "scheduled_at": null,
  "delay_seconds": null,
  "submission_nonce": "<opaque-client-nonce>",
  "honeypot": ""
}
```

Remove `startup_data` from the request. When startup runs, the response is:

```json
{
  "result": {},
  "startup_handle": "<opaque-random-handle>",
  "expires_at": "2026-08-04T12:00:00Z"
}
```

The browser may use `result` for rendering, but submission trusts only the
opaque handle. The handle is at least 256 bits of randomness and points to a
Redis record containing the form ID, organization, grant type, principal
binding, expiry, and authoritative startup output. Bind form sessions to their
token `jti`; bind an authenticated session to its user and organization. A
missing, expired, cross-form, or wrong-principal handle is rejected. Consume the
handle after an accepted public/HMAC submission; authenticated reuse follows
the existing repeat-submission behavior.

Use a discriminated response union:

```json
{
  "mode": "confirmation",
  "status": "accepted",
  "confirmation_markdown": "## Form submitted\n\nThank you!"
}
```

or, for an authenticated human principal only:

```json
{
  "mode": "execution",
  "status": "Running",
  "execution_id": "...",
  "workflow_id": "...",
  "workflow_name": "..."
}
```

The public confirmation response must never serialize an execution identifier,
workflow identifier, workflow output, error details, or polling URL. A public
success means that validation passed and Bifrost durably accepted the execution.
A synchronous workflow failure before durable acceptance returns a generic form
submission error and does not show the confirmation.

Public and HMAC sessions cannot schedule submissions. Reject `scheduled_at` and
`delay_seconds` with `422`. Authenticated forms retain existing scheduling.

## Data model and portability

### Portable form content

Add `confirmation_markdown` to `Form`, `FormCreate`, `FormUpdate`, and
`FormPublic`, defaulting to the standard confirmation. It is user-authored form
content and therefore must round-trip through:

- `ManifestForm`;
- manifest generation;
- form indexer import;
- Solution capture/export/install;
- local git sync and golden codec fixtures.

Do not place environment-specific publication configuration inside portable
form content.

### Environment-specific embed settings

Add a `form_publications` table:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `form_id` | UUID, unique FK | Cascade delete/update |
| `public_key` | string, unique | Opaque, URL-safe, rotatable |
| `allowed_origins` | JSONB list | Validated exact origins; empty means any |
| `approved_fingerprint` | string | Last explicitly reviewed capability set |
| `is_active` | bool | Publication state |
| `created_by` | UUID nullable | Audit attribution |
| `created_at` / `updated_at` | timestamptz | Lifecycle audit |

Publication state, public keys, allowed website origins, and HMAC secrets are
environment-specific. They must not be emitted into portable manifests or
Solution packages. Installing a Solution never publishes its forms
automatically.

## Authorization and capability policy

Replace the single path-only embed allowlist with an embed-kind, HTTP-method,
and exact-resource policy. Keep router-level checks as defense in depth.

| Capability | App embed | Form public/HMAC | Authenticated user |
|---|---:|---:|---:|
| Load bound form | Same-org behavior today | Exact `form_id` only | Form RBAC |
| Startup bound form | Same-org behavior today | Exact `form_id` only | Form RBAC |
| Field provider options | As needed by app | Exact form + named field | Form RBAC |
| Submit bound form | Same-org behavior today | Exact `form_id` only | Form RBAC |
| Upload for bound form | Same-org behavior today | Exact `form_id` only | Form RBAC |
| Generic workflow execute | Yes, existing app contract | **No** | Existing workflow RBAC |
| Execution REST reads | Own app executions today | **No** | Existing execution RBAC |
| Execution WebSocket | Existing app contract | **No** | Existing execution RBAC |
| Other form | Same-org app rule today | **No** | Form RBAC |
| Admin/SDK/config/table/knowledge APIs | No | **No** | Existing policies |

The policy must inspect tokens from every authentication location already
accepted by the application (`Authorization`, `access_token`, and
`embed_token`) so cookie replay cannot bypass the restriction.

## Server-side runtime rules

### Form access

Create one shared authorization helper for runtime actions:

- authenticated user: use the existing repository/RBAC rules;
- form session: require `embed_kind == "form"` and token `form_id` equal to the
  path form exactly;
- inactive form, inactive Solution, missing claim, cross-form, and cross-org
  attempts fail without leaking resource existence.

### Data-provider options

`POST /api/forms/{form_id}/fields/{field_name}/options` accepts only evaluated
input values:

```json
{"inputs": {"country": "US"}}
```

The server must:

1. authorize the form;
2. load the named `FormField` using `(form_id, name)`;
3. reject display-only fields and fields with no provider;
4. obtain `data_provider_id` from the row, never the request;
5. resolve it as an active workflow of type `data_provider`, anchored to the
   form's organization and Solution rules;
6. accept only provider input names configured on that field;
7. treat every browser-supplied value as untrusted;
8. return only normalized option fields used by the renderer;
9. project option metadata to only the keys referenced by that field's
   configured `auto_fill` mappings;
10. never include execution metadata, unused provider metadata, or internal
    provider errors.

Changing a field's provider changes the capability fingerprint. Public runtime
pauses until the administrator reviews and republishes; HMAC and authenticated
runtime follow their existing authorization independently.

### Startup workflow

The server chooses only `form.launch_workflow_id`. For form sessions:

- do not accept a workflow reference from the browser;
- run in the form's organization/Solution scope;
- merge trusted HMAC context separately from ordinary input;
- store the result in Redis behind a random opaque handle with the shorter of
  the form-session/access-token lifetime and the configured startup TTL;
- bind the record to the form, organization, grant, and principal as described
  in the submission contract;
- return the display result plus handle needed by the renderer;
- retrieve authoritative state only through the validated handle during
  submission;
- reject any client attempt to provide `startup_data`.

Publishing warns that startup output is visible to anonymous visitors.

### Submission workflow

Move the existing handler business logic to a shared form submission service.
The service must:

1. authorize the form and caller action;
2. validate `form_data` against the current server-side schema;
3. reject unknown/display-only fields and enforce type, size, option, and file
   constraints;
4. resolve only `form.workflow_id`, anchored to the form's organization and
   Solution;
5. construct separate namespaces for default configuration, untrusted submitted
   fields, trusted HMAC context, and server-held startup data;
6. atomically prevent a second successful submission for the same form-session
   `jti`/nonce;
7. create the execution and retain its internal form relationship;
8. return the principal-appropriate response without leaking public execution
   data.

The workflow execution context should expose:

```python
context.form_inputs     # validated visitor-entered fields
context.startup         # server-held launch workflow result
context.embed           # HMAC-verified values; empty for public/user sessions
```

Do not flatten these namespaces into one dictionary. If backward compatibility
requires continuing to pass form inputs as top-level workflow parameters, add
only validated `form_inputs`; never flatten `context.embed` over or under them.

### File uploads

For form sessions, an upload request must name a file field on the bound form.
Validate its MIME allowlist, size, and multiplicity server-side. Prefix object
keys with the form ID and session `jti`; accept only upload references minted for
that session during submission. A token for one form/session cannot mint or
attach another form/session's object key.

### Abuse controls

- Rate-limit public bootstrap by publication and client IP.
- Rate-limit provider, startup, upload, and submission actions by session `jti`
  and client IP.
- Include an off-screen honeypot in the public renderer; reject a populated
  value without invoking a workflow.
- Require a unique submission nonce and allow only one durably accepted
  submission per public/HMAC session.
- Bound body sizes, Markdown size, provider option count, and upload metadata.
- Emit structured audit/metrics events for bootstrap, rejection reason,
  throttling, provider calls, accepted submissions, and publication rotation.
- Do not claim these controls prevent a determined direct HTTP client.

## Browser origin and framing policy

Allowed origins are a browser-framing control, not authentication.

- Validate each configured value as an exact origin: scheme, hostname, and
  optional port only. Reject paths, credentials, fragments, control characters,
  and malformed wildcard forms.
- Empty origin list means `frame-ancestors *`.
- A non-empty list becomes an exact CSP `frame-ancestors` directive.
- Put the CSP header on the final embedded SPA document, not only the bootstrap
  redirect. The current redirect-only header does not establish a policy for the
  final document.
- Serve the dedicated `/embedded/forms/public/{public_key}` and
  `/embedded/forms/hmac/{form_id}` SPA documents through a small internal
  frame-policy endpoint. Production Nginx and the Vite debug server must both
  obtain and apply the same validated policy before serving the document.
  Public documents use the publication's exact origin list; HMAC documents
  preserve today's unrestricted framing until HMAC-specific origin settings are
  designed.
- Use `Origin`, `Referer`, and Fetch Metadata only as logged defense-in-depth
  signals; never treat them as the capability proof.
- Keep a strict parent/iframe `postMessage` origin check for resize and
  `form-submitted` notification events.

The iframe code generated by Bifrost is:

```html
<iframe
  src="https://bifrost.example/embed/forms/public/PUBLIC_KEY"
  title="FORM_NAME"
  loading="lazy"
  style="width:100%;min-height:640px;border:0"
  sandbox="allow-forms allow-scripts allow-same-origin"
></iframe>
```

Do not add `allow-top-navigation`; the confirmation remains inside the iframe.

## Confirmation experience

### Authoring

Add a **Confirmation message** Markdown field to form information:

- Default content is the standard confirmation above.
- Helper copy: `Shown after a successful public or embedded submission. Markdown and HTTPS images are supported.`
- Maximum length: 20,000 characters.
- Provide an adjacent preview using the same renderer as the runtime.
- Do not accept raw HTML.

### Runtime

After a public/HMAC submission is durably accepted:

- replace the form body with the rendered confirmation;
- retain the form title/branding context;
- scroll the iframe to the top;
- move programmatic focus to the confirmation container;
- announce success through a polite live region;
- disable resubmission for that session;
- send a minimal `postMessage` event containing only event type and public form
  identity, never submission data or execution identity.

On validation, throttling, network, or server failure, keep the form and show an
actionable form-level error. Never display workflow exceptions.

### Markdown safety

Use `react-markdown` without `rehypeRaw` and a shared confirmation renderer:

- allow ordinary Markdown and GFM formatting;
- allow `https://` images and same-origin images;
- render images responsively with `loading="lazy"`, useful alt text when supplied,
  and `referrerPolicy="no-referrer"`;
- reject or neutralize `javascript:`, `data:text/html`, raw HTML, event handlers,
  iframes, scripts, and SVG/HTML injection;
- open external links with `rel="noopener noreferrer"`;
- apply CSP suitable for externally hosted images without enabling external
  scripts.

Administrators should see a privacy note that an external image host receives a
request when the confirmation is displayed.

## Administrative UX

Expand the existing form Embed Settings section into two clearly separated
areas:

1. **Public website embed**
   - Unpublished/published status.
   - Publish review listing workflows, providers, metadata auto-fill, and files.
   - Allowed website origins.
   - Copyable iframe code after publication.
   - Rotate public link and unpublish actions with confirmation.
2. **Trusted HMAC integrations**
   - Existing secret creation, rotation/deactivation, and signing-scheme help.
   - Explain that HMAC is for systems that dynamically sign the iframe URL.

Exact primary copy:

| State | Heading | Supporting copy | Primary action |
|---|---|---|---|
| Unpublished | Public website embed | Allow anonymous visitors to load and submit this form from an iframe. | Publish form |
| Review | Review public form access | Publishing exposes the workflows and data sources listed below to anonymous form visitors. | Publish and copy code |
| Published | Public website embed | Anyone with this embed code can submit this form. Allowed websites control browser framing, not direct HTTP requests. | Copy embed code |
| Rotating | Rotate public link? | Existing embed codes will stop opening this form immediately. | Rotate link |
| Unpublishing | Unpublish form? | Existing public embed codes will stop opening this form. HMAC integrations are unchanged. | Unpublish |

## Implementation sequence

Each task includes its own targeted tests and leaves the branch green. Do not
parallelize writes to overlapping form/router/client files.

### Task 1: Characterize current behavior

- Add/extend tests that pin the existing authenticated and HMAC form flows,
  provider execution path, startup echo behavior, execution-history navigation,
  token cookie/header precedence, and cross-form binding.
- These tests establish what is intentionally preserved versus deliberately
  changed by later tasks.

Likely files:

- `api/tests/e2e/api/test_form_embed.py`
- `api/tests/e2e/api/test_embed_external_scope.py`
- `api/tests/e2e/api/test_forms.py`
- `client/src/components/forms/FormRenderer.test.tsx`
- `client/e2e/forms.user.spec.ts`

### Task 2: Add form content and publication persistence

- Add `confirmation_markdown` contracts, ORM column, migration, defaults, CRUD,
  form indexer handling, manifest codec handling, and Solution round trips.
- Add `FormPublication` ORM model, migration, repository/service, and admin
  contracts, including the approved capability fingerprint.
- Keep publication settings out of portable manifests.
- Update CLI/MCP DTO surfaces or explicitly classify any excluded publication
  operations; confirmation content must be supported wherever form create/update
  DTOs are supported.

Likely files:

- `api/src/models/contracts/forms.py`
- `api/src/models/orm/forms.py`
- `api/src/models/orm/form_publications.py`
- `api/alembic/versions/*_form_publication_and_confirmation.py`
- `api/bifrost/manifest.py`
- `api/src/services/manifest_generator.py`
- `api/src/services/file_storage/indexers/form.py`
- manifest/Solution golden and round-trip tests

Run DTO parity, skill-truth generation, and the contract fingerprint tripwire as
required by the repository contract rules.

### Task 3: Introduce typed form-session capabilities

- Add `embed_kind` and `grant` to token/principal contracts.
- Centralize public/HMAC form-session token minting.
- Reconcile the current `type="access"` router issuance and `type="embed"`
  helper into one tested token contract before changing middleware behavior.
- Split middleware policy by embed kind and exact method/path.
- Remove generic workflow execution, execution reads, and WebSocket access from
  form-session principals while preserving app embed behavior.
- Retain router-level exact-form checks.

Likely files:

- `api/src/core/principal.py`
- `api/src/core/embed_middleware.py`
- `api/src/core/security.py`
- `api/src/routers/embed.py`
- `api/tests/unit/core/test_embed_token.py`
- `api/tests/e2e/api/test_embed_external_scope.py`
- `api/tests/e2e/api/test_embed_workflow_execution.py`

### Task 4: Extract the unified form runtime service and move submission

- Move startup/submission workflow resolution and execution out of the router
  into shared business logic.
- Add the sanitized `FormRuntimeDefinition` endpoint and move every form
  renderer to it; keep the full form DTO on authenticated management screens.
- Rename the request contract and route from `/execute` to `/submissions`.
- Add server-side schema validation and separate trusted/untrusted contexts.
- Replace browser-echoed startup state with the opaque, principal-bound Redis
  handle and define its expiry/consumption behavior.
- Add confirmation/execution response variants and one-time session semantics.
- Update every in-repo caller and remove the old route rather than maintaining a
  fallback.

Likely files:

- `api/shared/form_runtime.py`
- `api/src/routers/forms.py`
- `api/src/models/contracts/forms.py`
- `api/src/sdk/context.py`
- `client/src/hooks/useForms.ts`
- `client/src/components/forms/FormRenderer.tsx`
- backend unit/E2E and frontend unit tests

### Task 5: Add field-derived provider options

- Add the field options contract and form route.
- Resolve the provider only through the stored field association.
- Enforce form organization/Solution resolution, configured input names, result
  normalization, auto-fill metadata projection, and public-safe error shaping.
- Change the form renderer to use this endpoint.
- Remove form usage of the generic provider execution helper; retain it for
  non-form callers.

Likely files:

- `api/shared/form_runtime.py`
- `api/src/routers/forms.py`
- `api/src/models/contracts/forms.py`
- `client/src/services/dataProviders.ts` or a new tested form-runtime service
- `client/src/components/forms/FormRenderer.tsx`
- `api/tests/e2e/api/test_form_provider_options.py`
- sibling client service/component tests

### Task 6: Add publication review, bootstrap, throttling, and frame policy

- Implement publication CRUD/review/key rotation.
- Refuse public publication of HTML/JSX display fields and surface the blocker
  in both the review response and admin UI.
- Enforce the approved capability fingerprint at bootstrap and on every public
  runtime action so an already-minted token cannot cross into changed powers.
- Implement public bootstrap and public session TTL.
- Add rate limits, nonce/idempotency storage, honeypot rejection, audit events,
  and inactive/unpublished behavior.
- Add the dedicated embedded SPA route and ensure both production Nginx and the
  Vite debug server apply the final-document CSP returned by the shared frame
  policy.
- Generate the copyable iframe.

Likely files:

- `api/src/routers/form_publications.py`
- `api/shared/form_publication.py`
- `api/src/routers/embed.py`
- `api/src/core/rate_limit.py`
- `api/src/core/embed_middleware.py`
- `client/nginx.conf`
- `client/vite.config.ts`
- publication/security E2E tests

### Task 7: Build confirmation authoring and runtime UI

- Add confirmation Markdown authoring and preview to form information.
- Add a shared safe `FormConfirmation` renderer.
- Render the confirmation for public responses without history navigation.
- Preserve authenticated execution navigation and HMAC access to only its exact
  submitted execution.
- Add accessible loading, validation, error, and success behavior.

Likely files:

- `client/src/components/forms/FormInfoDialog.tsx`
- `client/src/components/forms/FormInfoDialog.test.tsx`
- `client/src/components/forms/FormConfirmation.tsx`
- `client/src/components/forms/FormConfirmation.test.tsx`
- `client/src/components/forms/FormRenderer.tsx`
- `client/src/components/forms/FormRenderer.test.tsx`

### Task 8: Rework Embed Settings administration

- Add the public publication workflow, dependency review, origin editor, iframe
  copy, rotate, unpublish, and capability-change `Needs review` states.
- Preserve and relabel HMAC settings as trusted integrations.
- Add component coverage for every state and destructive confirmation.

Likely files:

- `client/src/components/forms/FormEmbedSection.tsx`
- `client/src/components/forms/FormEmbedSection.test.tsx`
- new tested client form-publication service module

### Task 9: Regenerate contracts, execute full verification, and document

- Regenerate `client/src/lib/v1.d.ts` from the running worktree API.
- Refresh CLI/MCP skill-truth appendices if form DTO flags change.
- Update API docs and user-facing embed guidance.
- Run the complete automated and live-stack verification below.

## Security test matrix

Every row is required automated coverage. Tests should assert both the status
code and the absence of sensitive response material.

| Threat/boundary | Required assertion |
|---|---|
| Public token -> other form | Cannot read, start, query, upload, or submit |
| HMAC token -> other form/org | Same denial on every form runtime route |
| Form token -> generic workflow endpoint | `403`; no workflow is enqueued |
| Form token -> execution REST/WS | `403`; no execution metadata disclosed |
| Form token replayed as either cookie | Same policy as bearer token |
| Public key unknown/inactive/rotated | Indistinguishable `404`; no session minted |
| Inactive form/Solution | Public and HMAC bootstrap/runtime denied |
| Capability changed after publish | Existing/new public sessions denied until reviewed and republished |
| Runtime definition disclosure | No workflow/provider/org/RBAC/audit/embed identifiers serialized |
| Provider substitution | Request cannot name a provider ID; altered field rejected |
| Provider wrong type/inactive/cross-scope | Rejected without executing |
| Provider input injection | Unconfigured keys rejected; values remain untrusted |
| Provider metadata over-return | Only keys named by the field's auto-fill mapping survive |
| Provider error | Generic options error; no exception/config output |
| Startup workflow substitution | Impossible; server reads only form binding |
| Forged startup result | `startup_data` rejected; only server-held handle output wins |
| Startup handle theft/cross-use | Wrong form, org, grant, principal, or expired handle rejected |
| HMAC/form input collision | `context.embed` retains signed value separately |
| Main workflow substitution | Impossible; request has no workflow reference |
| Unknown form keys | Rejected before workflow execution |
| Invalid type/option/size | Rejected server-side before execution |
| Scheduled public/HMAC submission | `422`; no scheduled execution created |
| Duplicate nonce/session submit | Exactly one execution accepted |
| Honeypot populated | Generic rejection; no execution created |
| Rate limit exceeded | `429`; no provider/startup/workflow invocation |
| Foreign upload reference | Rejected; cannot attach to submission |
| Markdown raw HTML/script URL | Not rendered/executed |
| Markdown HTTPS image | Renders responsively with no-referrer policy |
| Public form with HTML/JSX field | Publication is blocked server-side before a key is active |
| Origin header spoof via direct client | Never expands token scope; documented as non-auth |
| Disallowed browser ancestor | Final document blocked by `frame-ancestors` |
| App embed regression | Existing app render/workflow/execution scope remains green |
| Manifest/Solution round trip | Confirmation preserved; publication/key/origins omitted |

## Automated test plan

### Backend unit tests

- Token claim construction and grant-specific TTL.
- Exact method/path capability matrix for app versus form embeds.
- Origin parser and CSP formatter, including header-injection inputs.
- Publication dependency fingerprint stability/change detection.
- Classification of security-relevant versus cosmetic changes in that
  fingerprint.
- Runtime-definition projection excludes every management-only identifier.
- Server-side form submission schema validation.
- Startup-handle entropy, principal/form binding, expiry, and consumption.
- HMAC trusted-context separation and collision behavior.
- Markdown length/default contract validation.
- Form runtime service selection of startup, provider, and submission workflows.

### Backend E2E tests

- Publication lifecycle, rotation, unpublish, inactive form, and key lookup.
- Capability-change invalidation for both new and already-minted public tokens;
  cosmetic/confirmation edits remain live.
- Public bootstrap and token use across all allowed runtime actions.
- Existing Shopify/HaloPSA HMAC schemes through the same runtime.
- Full security matrix above using real PostgreSQL, Redis, queue, worker, and
  object storage.
- Exact provider invocation through a real provider-backed field.
- Provider metadata projection to only approved auto-fill targets.
- Exact submission workflow execution and internal form/execution relationship.
- Confirmation-only external response versus authenticated execution response.
- Rate limiting and one-time submission state in Redis.
- Upload signing and attachment isolation.
- Public HTML/JSX publication blocker, including a direct API attempt that
  bypasses the admin UI.
- Manifest, git sync, capture/export/install round trips.

Use `./test.sh`; never run pytest directly on the host.

### Client unit tests

- Form runtime service calls only `/api/forms/*` for
  runtime-definition/startup/options/upload/submit and never consumes the full
  management DTO.
- Public confirmation response never navigates to history.
- Authenticated and HMAC execution responses preserve authorized navigation;
  HMAC cannot read an unrelated execution.
- Confirmation Markdown formatting, HTTPS image, malicious URL/raw HTML, focus,
  live-region, and long-content behavior.
- Publication review, publish, copy, rotate, unpublish, and origin validation.
- Embed token consumption remains isolated to the iframe session.
- Provider field requests use form ID + field name, never provider ID.

Every new functional module under `client/src/lib` or `client/src/services` gets
a sibling test, including the storage/session boundary where applicable.

### Playwright happy path

Add exactly one primary user-flow spec:

```text
client/e2e/forms-public.unauth.spec.ts
```

The spec must use live services and a second-origin fixture host:

1. Create/register a real data provider and submission workflow.
2. Create a form with a provider-backed select, ordinary required fields, and
   confirmation Markdown containing formatting and an HTTPS image served by the
   controlled local HTTPS fixture host (no public-internet dependency).
3. Publish through the admin UI after reviewing dependencies.
4. Copy/use the generated iframe on the second-origin host.
5. Open a fresh unauthenticated browser context.
6. Verify the iframe loads without a Bifrost login.
7. Verify provider options load through the form field endpoint.
8. Fill and submit the form.
9. Verify the confirmation replaces the form, Markdown and image render, focus
   moves to success, and no history/execution UI appears.
10. Verify the network contains no form-session request to
    `/api/workflows/execute`, `/api/executions/*`, or `/ws`.

Use semantic selectors and condition-based waits only. Do not add retries or
timeouts to mask state pollution.

## Live-stack acceptance and UX review

Implementation is not complete after automated tests. Exercise the actual
worktree stack:

1. Start and verify the worktree stack with `./debug.sh up` and
   `./debug.sh status`.
2. Connect an API-matched CLI from an isolated `/tmp/bifrost-cli-<name>` virtual
   environment, following the repository debug instructions.
3. Seed a real provider workflow, startup workflow, submission workflow, and
   form with enough fields to exercise scrolling, validation, dynamic options,
   auto-fill metadata, and a file upload.
4. Configure the Markdown confirmation in the UI.
5. Publish and inspect the dependency review for correctness.
6. Load the generated iframe from the second-origin fixture and complete it as
   an unauthenticated visitor.
7. Inspect browser network requests and final response headers.
8. Attempt direct token misuse against another form, generic workflow execute,
   executions, WebSocket, admin, SDK config, tables, and knowledge endpoints.
9. Change the published form's provider binding and verify both an existing
   public session and a new bootstrap are denied until the changed dependency is
   reviewed and republished; verify a confirmation-only edit remains live.
10. Rotate the key while the old iframe is open; verify old bootstrap fails and
   the already-minted session expires naturally without gaining new authority.
11. Unpublish and verify both new and already-minted public sessions stop, then
    complete an HMAC submission and its scoped execution result to prove HMAC
    embedding still works.
12. Run the Playwright spec with `--screenshots`, view every captured screenshot,
    and correct spacing, responsiveness, focus visibility, contrast, image
    overflow, and long-Markdown issues.
13. Verify narrow mobile, typical desktop, and constrained iframe heights.

Record the debug URL, seeded entity IDs, browser observations, security-smoke
results, and screenshot review in the implementing PR or a companion validation
log.

## Required pre-completion commands

From the implementation worktree:

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
./test.sh quality api

cd client
npm run generate:types
npm run tsc
npm run lint
cd ..

./test.sh stack up
./test.sh all
./test.sh client unit
./test.sh client e2e
./test.sh client e2e --screenshots client/e2e/forms-public.unauth.spec.ts
```

Also run the targeted DTO parity and contract-version tests after model changes.
No test may be skipped, marked xfail, retried, or muted to complete the work.

## Rollout and compatibility

- Ship the database migration before application code that reads publication or
  confirmation fields.
- Existing forms receive the default confirmation Markdown and remain
  unpublished.
- Existing HMAC secrets remain valid and continue using their configured signing
  schemes.
- Existing HMAC iframe URLs continue to bootstrap, but their new form-session
  tokens lose generic workflow and execution access by design.
- Update every in-repo form caller to `/submissions` in the same change that
  removes `/execute`; do not leave a compatibility shim.
- Regenerate TypeScript types and update contract fingerprints. A route rename
  is loud (`404`) rather than silently corrupting, but release notes must call
  out the API change for external clients.
- Monitor denial and submission error metrics after rollout. Unexpected form
  attempts against generic workflow/execution endpoints indicate clients that
  depended on the overly broad old token.

## Measurement and instrumentation

Primary success:

- an administrator can publish, copy an iframe, and receive a real anonymous
  submission without configuring HMAC or exposing execution details.

Counter-metrics:

- no increase in failed authenticated form submissions;
- no cross-form or generic-workflow access by form-session tokens;
- provider/startup latency remains within current form-load expectations;
- confirmation images do not cause layout overflow;
- public abuse does not produce unbounded workflow volume.

Emit metrics/audit events for publication enabled/disabled/rotated, public/HMAC
session minted/rejected, provider requested/rejected, startup requested/rejected,
submission accepted/validation-rejected/rate-limited/duplicate, and confirmation
shown. Never log tokens, HMACs, submitted field values, or full public keys.

## Ethical and privacy review

- The UI explicitly states that anyone with the public embed code can submit.
- Origin allowlists are not described as protection from direct HTTP clients.
- Publication requires reviewing the data-producing dependencies exposed to
  anonymous visitors.
- External confirmation images carry a privacy notice because their host can
  observe the image request.
- There is no deceptive success state: confirmation appears only after Bifrost
  durably accepts the submission.
- The plan introduces no forced redirect, hidden subscription, preselected
  consent, or manipulative confirmation pattern.

## Risks and mitigations

1. **Provider/startup data disclosure.** Mitigated by explicit publication
   dependency review, exact form binding, and operator-visible warnings.
2. **Form changes after publishing.** Safe content changes update live, while
   capability changes fail closed through the stored fingerprint and a visible
   `Needs review` state. Immutable full revisions can follow later.
3. **Public automation.** Mitigated, not eliminated, by self-hosted proof-of-
   work, rate limits, one-time sessions, nonce/idempotency, honeypot, validation,
   and observability.
4. **Breaking external `/execute` clients.** Mitigated by release notes and a
   single coordinated rename; no indefinite duplicate route.
5. **HMAC integration assumptions about execution reads.** Register only the
   submitted execution against the HMAC session, instrument denied unrelated
   reads during rollout, and document the scoped result behavior.
6. **Dynamic frame policy divergence between Vite and Nginx.** Both must consume
   the same server-generated policy and receive explicit response-header tests.

## Definition of done

- All runtime form calls are under `/api/forms/{form_id}/*`.
- Renderers consume a sanitized runtime definition; management-only workflow,
  provider, organization, RBAC, audit, and embed identifiers are absent.
- No form-session principal can call generic workflow execution. Public
  sessions cannot access execution APIs; HMAC sessions can access only the
  execution registered by their own submission.
- Provider invocation is derived from an authorized field association, and
  returned metadata is limited to approved auto-fill targets.
- Public submissions expose no execution information; HMAC submissions expose
  only their exact execution result.
- HMAC signed context cannot be overwritten by form input.
- Browser-provided startup data cannot alter execution context; authoritative
  startup output is retrieved through a bound, expiring opaque handle.
- Public submissions are schema-validated and idempotent server-side.
- Public submissions require a short-lived, exact-form/session-bound,
  single-use proof when Spam Protection is enabled.
- Confirmation Markdown is portable, customizable, safely rendered, accessible,
  and supports HTTPS images.
- Publication settings remain environment-specific and are absent from portable
  exports/Solutions.
- Public capability changes invalidate existing/new sessions until reviewed;
  public HTML/JSX fields cannot be published.
- Public framing policy is applied to the final document.
- Targeted, full backend, full client unit, and Playwright suites pass with no
  skips or retries.
- The live second-origin iframe journey and adversarial security smoke pass.
- Screenshots have been visually reviewed and UX defects corrected.
