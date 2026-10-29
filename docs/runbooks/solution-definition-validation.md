# Solution Definition Validation

Solution package preview, ZIP install, CLI deploy, and connected Git deploy
read one `SolutionDefinition` before their adapters collect entities. Common
`.bifrost/*.yaml` files use the workspace manifest document decoder and the
existing `Manifest*` models, so duplicate keys, unexpected wrappers, unknown
fixed fields, and malformed entity mappings fail before deployment planning or
file projection.

Config declarations and connection declarations remain explicit Solution
schemas. Package transport fields are also explicit: app prebuilt dist/SDK
metadata, form workflow bindings, agent run timeout, and workflow source. Open
payload dictionaries such as schemas, adapter settings, and templates remain
open where their models already allow them. A package cannot use transport
fields to bypass common-entity validation.

Empty and comment-only manifest files remain empty. Existing map-key identity
defaults and supported aliases are normalized through the common models: table
`table_schema`/`schema` and policy `ref`/`$ref` are accepted. Descriptor
`scope` remains ignored for legacy packages, while `global_repo_access` remains
the accepted alias for `allow_outbound_access`.

When diagnosing a rejected package, correct the workspace file named in the
error and rebuild the ZIP. The reader reports the source manifest, field path,
and YAML line when available. It does not write, reconcile, compile, or publish
anything. Resource effects stay in the existing Solution deployer.

## Verification

`api/tests/unit/test_solution_definition.py` covers the shared workspace and
Solution failure boundary, map-key aliases, typed transports, and declaration
defaults. `test_solution_export.py`, `test_solution_zip_install.py`, and
`test_solution_git_sync.py` cover generated package, ZIP preview, and
connected-Git adapters. These tests enqueue no deploy or build jobs.
