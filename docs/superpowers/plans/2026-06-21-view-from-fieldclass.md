# Unify `view(dest)` on FieldClass policy (kill the install allowlists)

## Why

`view(INSTALL)` for Workflow/Form/Agent/App used four hand-maintained frozenset
allowlists — a manual restatement of what the field-class metadata already
encodes. They drifted from the metadata (bug **B2**: `tool_description` tagged
`CONTENT` but missing from the allowlist → dropped on install), and the diff
showed more silent disagreements (`mcp_connection_ids`, `logo`, `path`). The
per-destination policy already exists as DATA in `tests/roundtrip/paths.py`
(`Policy = dict[FieldClass, str]`) but only drives the TEST; production
reimplements it as the frozensets. That split is the whole bug class.

Decision (Jack): make **all** entities work the same way — one `view()`, driven
by per-field metadata. No per-entity `_install_view`, no frozensets, no
raw-snapshot methods. Every exception becomes per-field metadata, where it lives
next to the field.

## Design

### One policy, in production code

Move the policy into `api/bifrost/manifest_codec.py` (so prod + test share ONE
object):

```
Action = keep | drop | scrub | stamp | remap   # remap/stamp/scrub only affect importers; for view(), keep-vs-drop is what matters
POLICY: dict[Destination, dict[FieldClass, Action]]
  GIT_SYNC: IDENTITY=keep CONTENT=keep ENVIRONMENT=keep  SECRET=scrub REFERENCE=keep
  INSTALL : IDENTITY=keep CONTENT=keep ENVIRONMENT=drop  SECRET=scrub REFERENCE=keep
```

For `view()`, the only question is **keep vs not-emit**. GIT_SYNC keeps
everything (whole-model dump, Nones included — unchanged). INSTALL keeps
IDENTITY/CONTENT/REFERENCE, drops ENVIRONMENT + SECRET, drop-none.

### Per-field overrides via `classify()`

Add one optional modifier carrying a per-destination action override. The
exceptions found in the audit, each next to its field:

| field | class | install override | why |
|---|---|---|---|
| roles, role_names | ENVIRONMENT | **keep** | deploy needs the grant list (env binding that travels) |
| Form.path, Agent.path | CONTENT | **drop** | deprecated; content is inline |
| Agent.mcp_connection_ids | REFERENCE | **drop** | env-grant deployed via `_sync_agent_mcp_connections`, not the agent entry |
| App.path | CONTENT | **drop** | capture emits `repo_path` transport extra instead |

### Transport tiers stay generic

`extras` (repo_path, logo_b64, src_files, workflow_path, max_run_timeout, …) are
already merged generically by the base `view()` (`out.update(drop_none(extras))`).
No per-entity code — the caller passes them, the base merges them. App needs NO
bespoke method.

### Raw-snapshot entities (Table/Claim/ConfigSchema/EventSource)

Their current `_install_view` returns `{k:v for k in raw.items() if v is not None}`
where `raw` is built from the SAME model fields the policy would select. Re-derive
from the policy; the `_raw_policies`/`_raw_*` snapshots become CONTENT fields the
policy keeps (or transport extras). EventSource already returns the full dump
(keeps all) — the policy's INSTALL drop-ENVIRONMENT must reproduce its current
output (org_id is stamped, which for view() = emitted; verify against golden).

### Result

`EntityCodec.view(dest)` is the ONLY serializer:
```
def view(dest):
    out = {}
    for name, f in model_fields:
        action = field_override(name, dest) or POLICY[dest][class_of(f)]
        if action in EMIT_ACTIONS and (dest is GIT_SYNC or value is not None):
            out[alias_or_name] = value
    out.update(drop_none(extras))   # GIT_SYNC: no extras
    return out
```
Delete: 4 frozensets, 4 `_install_view` overrides (Workflow/Form/Agent/App),
4 raw-snapshot `_install_view` (Table/Claim/ConfigSchema/EventSource). The
structural guard + goldens + roundtrip detector prove byte-identity.

## Risk

A field with the WRONG class flips behavior and a golden that seeds it as the
default won't catch it. Mitigated by: the field-class tripwire test, the
structural guard, and re-running the full detector. Any golden that DOES change
is a place the allowlist already silently disagreed with the metadata — surface
and confirm each.

## Verification

Per entity: `view(INSTALL)` field-set == today's allowlist set (byte-identity
target captured pre-refactor). Then: codec goldens ×2, roundtrip detector 25/25,
full `./test.sh all`, pyright/ruff.
