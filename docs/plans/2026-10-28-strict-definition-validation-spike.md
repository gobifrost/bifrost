# Strict Definition Validation Spike

For [#1098](https://github.com/gobifrost/bifrost/issues/1098), based on
`ec7f02f5baa6393b9c2417978914429990c004c7`.

## Decision

Use one shared, pure definition-validation layer in the packaged `bifrost`
library. Invoke it when accepting writes, imports and deployments, before
projection or mutation. There is no opt-in strict flag. Unknown configuration
names reject the candidate; supported deprecated names get explicit migration
handling. Keep execution-time parsing of unchanged deployed source unchanged.

The existing `ManifestResolver` is the right reconciliation owner, but cannot be
the only validation hook: its input is already a typed `Manifest`. Parsers and
Solution collectors can discard unknown keys before it sees them. Solution
lifecycle reconciliation also has its own `SolutionDeployer`; moving that into
the workspace resolver is unnecessary for this change.

Here strict means rejecting unknown field names, not changing every existing
type-coercion rule. Open dictionaries containing customer data remain open.

## What This Spike Proves

`api/bifrost/definition_validation.py` provides a testable prototype. It is not
called by production acceptance or runtime paths.

- Pydantic's per-call `model_validate(..., extra="forbid")` rejects unknown keys
  recursively using existing models. It does not change their default parsing
  policy. A regression test verifies normal parsing before and after the call.
- A typo such as `events.<id>.subscriptions[0].criteria` is rejected at its field
  path rather than disappearing during parsing. Existing event export/install
  golden fixtures still pass strict entity validation.
- Wrapper keys must be checked before extracting an individual section. An
  `events.yaml` containing `evnts`, or a different recognized section, is invalid.
  `files.yaml` needs its existing direct-mapping treatment.
- Nonempty scalar, list, boolean and null documents cannot masquerade as empty
  manifests. Explicit duplicate scalar YAML keys are detected before loading
  overwrites them; ordinary YAML merge overrides remain usable.
- Arbitrary `webhook_config` and subscription `input_mapping` data remain valid.
  Unknown rejected values are omitted from the prototype's diagnostics.
- Existing `ManifestSolutionConfigSchema` handles Solution declarations; those
  declarations correctly fail the workspace config-value schema.
- The app install golden fixture cannot use `ManifestApp` directly: `path` is
  missing, while valid transport fields `repo_path`, `logo_b64` and
  `logo_content_type` are extras. This is evidence for a package schema, not a
  reason to permit arbitrary extras.
- The descriptor's explicit `global_repo_access` alias still works; a misspelled
  `allow_outbond_access` is rejected.
- An AST prototype can check decorator keyword names without executing submitted
  Python, even when values are expressions. It resolves common Bifrost imports
  and aliases and derives keywords from the real signatures for `workflow`,
  `tool`, `data_provider` and `service`. Literal `**{...}` keys are checked;
  dynamic expansions receive an unverifiable-keywords error in this prototype.

## Acceptance Map

| Entry point / owner | Existing use | Required integration |
| --- | --- | --- |
| Entity CRUD / `RepoSyncWriter` | DB records generate `_repo/.bifrost/` exports | Keep DTO and authorization behavior; check generated definitions in round-trip tests. Do not reinterpret manifests as the normal entity mutation API. |
| `bifrost.manifest.parse_manifest`, `parse_manifest_dir`, `read_manifest_from_dir` | Full/split workspace manifest reading | Use the shared raw reader before section selection and model construction. Preserve cross-reference checks. |
| `GitHubSyncService.prepare_desktop_sync`, `apply_desktop_sync`, `reimport_from_repo` | Candidate manifests drive upserts, stale deletion and source publication | Validate the full candidate before import/deletion operations or source activation. Keep fingerprint and stale-plan protection. Reimport is an explicit acceptance operation; generic runtime/indexer reads are not. |
| CLI `commands/solution.py` collectors; server `solutions/zip_install.py::_parse_workspace` | Shared collectors narrow YAML into deployment dictionaries | Replace raw collection with a shared validated Solution reader before narrowing. Check both source definitions and final build overlays. Server repeats authoritative validation of uploaded inputs. |
| `solutions/git_sync.py` | Connected updates build a deploy ZIP; another `read_workspace_bundle` reader also exists | Feed both readers through the same package validation. Preserve Solution scope/version/one-writer semantics. |
| Workspace package projection and importer | Package preview -> projection -> planner -> `ManifestResolver.plan_partial_import` -> staged source promotion | Validate raw package before projection and the selected candidate before resource mutations; preserve hash binding, keep/replace decisions and environment restamping. |
| `file_storage/file_ops.py::FileOperationsService.write_file` | Editor/API/SDK active writes | Validate Python before S3 writes, index flush, Redis `set_module`, metadata extraction and notifications. Current metadata extraction occurs after those writes. Reuse the existing AST where feasible. |
| `FileIndexService.write_file` | Hash-bound staged package promotion | Ensure the entire candidate was checked before importer mutations, and verify the exact source being promoted. |
| Files signed-URL routes | Direct PUT can overwrite storage without the API seeing bytes | Refuse active-definition signed PUT before issuing a URL and use the validated write API. Ordinary data uploads remain supported. A future direct-definition upload would need staging and validated promotion. |
| CLI push/watch | Writes ordinary code; `.bifrost/` is excluded | Precheck selected source before sending, enforce server writes, and retain export-only manifest behavior. This does not make independent HTTP writes transactional. |
| `ManifestResolver` / `SolutionDeployer` | Central resource application | Validate incoming candidates before first mutations as a backstop for direct service callers, in addition to raw-reader enforcement. |

## Work Needed Before Enforcement

1. **Finish schema inventory and shared readers.** Explicitly model Solution
   config/connection declarations and actual workflow/app package additions:
   `source`, `repo_path`, logo bytes, `src_files`, `bin_files`, `dist_files`,
   `bin_dist_files`, and `prebuilt_sdk_metadata`. Reuse existing entity models
   and codec field metadata. Preserve prebuilt-only App exports and portable
   form/agent content. Do not strip valid transport fields to satisfy a schema.
2. **Record real deprecations.** Classify supported aliases and removed settings
   from their actual history. `global_repo_access` has an explicit alias;
   descriptor `scope` is currently removed by a pre-validator and needs a
   deliberate migration diagnostic. Passing `extra="forbid"` alone cannot catch
   keys a pre-validator removes. Historical silent ignore is not support.
3. **Complete static source resolution.** The AST experiment does not model
   local rebinding, star imports, imports in nested scopes or indirect factories.
   Resolve these rules and check first-party examples before enforcement. Do not
   claim the experiment catches all decorators. Decide/document the proposed
   refusal of dynamic `**options` instead of executing user code to inspect it.
4. **Wire each acceptance boundary.** Put shared checks before mutations and
   preserve current authorization, staging and publication. Adapt errors to
   existing file, preflight, Solution preview and PlatformJob contracts. No new
   validation service, job type or browser polling mechanism is needed.
5. **Prove rejection preserves active state.** Add targeted integration tests
   for a rejected source overwrite, Git candidate including deletion planning,
   Solution candidate and staged package import. Assert previous source/cache
   and entities survive. Exercise ordinary valid exports/deployments too.

The YAML prototype also needs production diagnostics: field source marks,
bounded document processing, and duplicate-key handling for YAML's equivalent
scalar spellings (for example `true` and `True`). Current duplicate detection
compares explicit scalar tags/text, not all constructed-key equivalences.
Alias/merge behavior needs characterization against supported manifests.

## Size and Delivery

Allow **3–5 engineering days** for production integration, tests and review,
assuming package/deprecation inventory reveals no broader schema defects. The
pure unknown-field check is small; most effort is covering the distinct
acceptance paths and proving invalid changes leave active state intact.

Suggested review slices:

1. Strict manifest/descriptor/package readers, raw Git/Solution hooks and
   preservation tests. This provides manifest acceptance enforcement.
2. Decorator source validation, write/promotion/signed-upload hooks and source
   preservation tests. This closes the Python configuration boundary.

Both slices share the pure validator. Neither changes execution-time decorators
to reject unchanged deployed workflows. No production migration, deploy or
runtime hook is included in this spike.

## Verification

Run in this worktree through the Docker test harness:

```sh
./test.sh tests/unit/test_definition_validation.py tests/unit/test_manifest.py tests/unit/test_manifest_codec.py tests/unit/test_solution_descriptor.py -v
./test.sh quality api
git diff --check
```

Results: **214 passed, 0 failed, 0 skipped** (JUnit, 3.634 seconds). Existing
codec tests emitted 52 Pathspec deprecation warnings. API quality passed with
zero Pyright errors/warnings and Ruff clean; `git diff --check` passed.

No live acceptance integration, full backend, client or browser suite was run
for this spike; production acceptance paths are deliberately unmodified.
