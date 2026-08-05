# Public form embeds

Public form embeds let an administrator publish one form as a copyable iframe
without distributing a signing secret. HMAC embeds remain available for trusted
systems that dynamically sign query parameters. Both grants converge on the
same form runtime.

## Runtime surface

An issued form session can call only these routes for its bound form:

- `GET /api/forms/{form_id}/runtime`
- `POST /api/forms/{form_id}/startup`
- `POST /api/forms/{form_id}/captcha/challenge`
- `POST /api/forms/{form_id}/fields/{field_name}/options`
- `POST /api/forms/{form_id}/upload`
- `POST /api/forms/{form_id}/submissions`

It cannot call generic workflow execution, management form endpoints, or
another form. Public submissions receive only the configured confirmation.
HMAC submissions receive an execution ID and may read or stream only that
session's exact execution; the HMAC token cannot list history or read an
unrelated execution. Signed HMAC values are available to workflows under
`context.embed`; validated visitor fields are under `context.form_inputs`;
server-held launch output is under `context.startup`.

## Publishing and revocation

Use **Share Form** from either the Forms list or the form editor toolbar. The
dialog separates **Private Link**, **Website Embed**, and **HMAC** into tabs.
Recipients of the private `/execute/{form_id}` link must sign in and already
have access to the form. For public embedding, review the submission workflow,
optional startup workflow, provider-backed fields, metadata projection, and
file fields, enter exact allowed origins if browser framing should be
restricted, then confirm anonymous access and copy the iframe code. The HMAC
tab manages shared secrets and signed-query integration examples for trusted
systems.

The **Confirmation Message** editor is in the **Website Embed** tab. It uses the
shared rich Markdown editor with an explicit runtime preview, supports HTTPS
images, and applies only to anonymous public submissions. HMAC and private-link
submissions navigate to their authorized execution result instead.

**Spam Protection** is enabled by default for public embeds. It uses a
self-hosted ALTCHA proof-of-work challenge: visitors do not contact a CAPTCHA
vendor, operators do not need a vendor account, and there is no CAPTCHA-specific
environment variable. Turning it off is an immediate publication update and
does not affect HMAC or authenticated forms.

## Appearance parameters

The embed is an isolated iframe document, so host-page CSS does not cascade
into it. The document contains the optional form name/description header, the
form card, and a canvas filling the iframe rectangle. New embeds use a light,
solid canvas by default.

The iframe `src` accepts these presentation-only query parameters:

- `theme=light|dark|system` selects the form controls and canvas palette.
  `system` follows the visitor's browser/OS preference, not the host site's CSS.
- `header=false` hides the form name and description. The default is `true`.
- `background=transparent` makes the iframe canvas transparent while retaining
  the selected theme for text and controls. The default is `solid`; choose a
  theme with enough contrast against the host page when using transparency.

These reserved parameters do not enter form inputs or HMAC workflow context.
For HMAC embeds they are included in the signed query, then removed from the
verified workflow context after signature validation.

Capability-affecting edits invalidate public access until an administrator
reviews and republishes. Copy, description, and confirmation-message edits stay
live. Rotating a public link invalidates the old public key. Unpublishing also
revokes already-issued public sessions on their next form action. HMAC sessions
are independent of public publication settings.

The publication switch is the enable/disable/review control. Allowed-origin
changes save automatically while published and are included when first
publishing. There is no separate generic Update action. Rotate is intentionally
explicit because it invalidates every existing public embed code.

Publication keys, origin settings, and approval fingerprints are
environment-specific and do not enter portable form manifests or Solutions.
The confirmation Markdown does round-trip with the form.

## Security model and limits

`frame-ancestors` is applied to the final embedded document. An empty origin
list permits any framing origin; a non-empty list permits only the exact
canonical origins shown in settings. This is a browser control, not proof that
a request came from a browser. PowerShell, curl, and other direct clients can
still use a public form and can implement the public proof-of-work algorithm.
Spam Protection raises the cost of automated submissions; it does not turn a
public URL into client authentication. The server also enforces exact-form
capabilities, authoritative schema validation, field-derived providers,
session-owned file paths, throttling, a honeypot, and one accepted submission
per short-lived session.

Each challenge expires after five minutes and is signed for one exact form and
public-session ID. The proof is atomically redeemed in Redis, so replay and
cross-session reuse fail. Bifrost derives the signing key from the already
required `BIFROST_SECRET_KEY` with domain-separated HKDF. A random startup key
would break active challenges on restart and differ between replicas, while a
Redis-only secret would add unnecessary shared-secret lifecycle. Keep
`BIFROST_SECRET_KEY` stable and private as required for the rest of Bifrost.

Never place secrets in confirmation Markdown, provider option metadata, form
labels, or error messages. External Markdown images are fetched by each
visitor's browser; their host receives the browser request.

An HMAC secret authorizes the host to mint a scoped form session; it does not
grant general Bifrost access. The execution-result capability is registered
after the session submits and expires with the embed session. Do not treat an
HMAC iframe URL as public—the signed URL can be exchanged for that scoped
session until its query values or secret are rotated.

## Operational checks

For a reported embed failure:

1. Read `GET /api/forms/{form_id}/publication` as a platform administrator.
2. If status is `needs_review`, inspect `publication-review` and republish.
3. Confirm the form, its Solution install, and every reviewed workflow/provider
   are active.
4. Request `/embed/forms/public/{public_key}` without following redirects. A
   valid publication returns `302`; unknown, rotated, inactive, and unpublished
   keys return the same `404`.
5. Request `/embed/forms/public/{public_key}/frame-policy` and verify the CSP
   contains the intended exact origins.
6. Inspect structured logs by `form_id` for publication, session issuance,
   CAPTCHA challenge/rejection, provider, startup-handle, honeypot, duplicate,
   throttling, or accepted-submit events. Tokens, proof payloads, field values,
   HMAC values, and full public keys must not be logged.

HTTP `429` means the publication/IP or session/IP limit was exceeded. Respect
the `Retry-After` header. A `409` on submission means that form session already
accepted a submission; reload the iframe to begin a new session.
`Verification is required` means Spam Protection is enabled but no proof was
submitted. `Verification is invalid or expired` covers invalid, expired,
cross-form, and cross-session proofs; retry the checkbox to request a fresh
challenge. `Verification has already been used` is a replay rejection.

## Rollout and rollback

Apply both public-form migrations before deploying code that reads form
publication, confirmation, or Spam Protection fields. During rollout, verify
an authenticated form still
navigates to execution history, a public iframe shows inline confirmation, and
an enabled public CAPTCHA gates submission, and an HMAC iframe bypasses CAPTCHA
and navigates to its own result while receiving `403` for an unrelated
execution ID.

To disable public access without affecting normal or HMAC forms, unpublish the
form. For a broader rollback, disable public bootstrap at the routing layer
while leaving the publication table intact; this is recoverable and preserves
administrator settings. Do not roll back the migration while application code
still reads the new columns. Once all application instances run older code, the
migration downgrades remove the Spam Protection setting, public publication
records, and the confirmation column, so export any confirmation content first
if it must be retained.
