# Workspace Manifest Validation

Workspace Git sync validates recognized `.bifrost/*.yaml` files before applying
entity changes, planning deletions or publishing source. Legacy `metadata.yaml`
uses the same validation. There is no permissive validation mode.

The reader rejects unknown schema fields, unexpected split-file sections,
duplicate YAML keys and non-mapping documents. Errors identify the manifest and
field path or YAML line. For example, `timeout_second` on a workflow is rejected;
the supported setting is `timeout_seconds`. Correct the repository file and
retry the sync. Do not use Reimport to repair a typo in an incoming repository.

This checks field names against the existing manifest models. It retains their
type conversion rules and explicit aliases. User-data dictionaries, including
config values, webhook adapter settings and subscription input mappings, remain
open. Ordinary YAML merge overrides remain supported. Missing, blank and
comment-only files keep their existing empty-document behavior; an explicit
`null`, list or scalar is not an empty manifest.

Split files contain only their own section, such as `events:` in `events.yaml`.
`files.yaml` is a direct mapping with fields such as `locations:`. If both split
files and legacy `metadata.yaml` exist, the existing split-file precedence is
unchanged. `.bifrost/` remains an export surface for CLI push/watch; watch does
not apply manifest edits.

The shared reader is used by Git preflight, sync/connect and explicit reimport.
Reimport still generates its manifest from the current database. Validation
does not run when workers import already deployed Python, and this change does
not alter workflow decorators.

This is the workspace-manifest slice of
[#1098](https://github.com/gobifrost/bifrost/issues/1098). Solution package and
descriptor validation, explicit migration diagnostics for removed settings, and
Python decorator validation at write/deploy acceptance remain separate work.
Solution declarations and transport payloads require their own schemas; a
workspace schema must not be applied to them unchanged.

## Implementation and Verification

`bifrost.definition_validation` safely decodes YAML before the workspace reader
projects sections or constructs `Manifest`. It applies Pydantic's per-call
unknown-field prohibition without changing default model construction. The
distributed CLI requires Pydantic 2.12 or later to support that call.

The Git publication-ordering regression seeds active database, file-index,
object-storage and Redis state, then submits a malformed remote candidate. It
asserts rejection before resolver import/deletion calls or publication, and
checks that active entities and source remain intact. It queues no platform
jobs or workflow executions. Existing valid pull and validation-savepoint tests
exercise successful acceptance.
