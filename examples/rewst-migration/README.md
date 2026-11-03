# Rewst Migration pilot

An installable Bifrost Solution for collecting Rewst definitions and keeping a
migration plan, inventory, and verification evidence available to your own coding
agent. Codex or Claude builds the replacement automations in your repository;
this Solution does not require Bifrost's internal Coding Agent or an AI provider.

**Pilot status:** the synthetic flow can run without a Rewst account. The live
adapter uses historical private GraphQL queries and has not been validated
against a current Rewst account. A successful export is not proof that every
resource was recovered. Coverage gaps remain visible in the migration record.

## Install

With the updated Bifrost plugin, invoke `rewst-migration` in Codex or Claude to
walk through setup, installation, the sample, and live extraction. The direct
installation steps below are the same path used by the skill.

Use an up-to-date CLI downloaded from your Bifrost instance and sign in as an
administrator. Choose a private Bifrost test organization for this pilot.
The instance must support repository-installed Solutions, V2 Apps, install-scoped
config declarations, and managed-file revision guards (`expected_version` and
`create_only`). Older builds missing these contracts are not supported.
The pilot also needs the standalone CLI location-validation and runtime-file
update fixes included in [PR #1222](https://github.com/gobifrost/bifrost/pull/1222).
Use a Bifrost build containing those fixes; installing the Solution alone does
not update the platform. An earlier affected build can create a record but fail
on its next state-file write.

```bash
bifrost solution install-repo https://github.com/gobifrost/bifrost \
  --subpath examples/rewst-migration \
  --ref main
```

The pilot follows the development branch. Record the installed Git commit when
reporting a result, or pin `--ref` to a tested commit for a repeatable installation.
Repository installs are Git-managed; customize a fork rather than editing
managed definitions on the instance. Installation remaps portable definition
identities and creates fresh runtime storage. No previous customer's migration
state, exports, organization mappings, or credentials are included.

Open **Solutions → Rewst Migration → Contents**, then open the **Rewst Migration**
app. The app and workflows are administrator-only for the pilot. Do not broaden
access to raw definitions without reviewing their contents and storage policies.

## Start with the sample

1. Choose **Try a Sample Migration**. No cookie, network call to Rewst, or AI
   subscription is needed.
2. Export the sample environment. Review the inventory, source artifacts, and
   coverage gaps. The sample badge remains visible throughout the app.
3. Open **Plan**, add a sandbox acceptance case and operating limits, and save.
4. Record a status change and test evidence for an inventory item. Evidence is a
   statement supplied by the operator or coding agent, not an independent proof
   that the replacement works.
5. Open **Agent Handoff**. Copy the prompt and commands into your coding agent,
   authenticated to the same Bifrost instance. Retrieve the saved context, then
   close and reopen the app to check that the plan and evidence persist.

The sample verifies the package's mechanics. It does not verify Rewst extraction,
replacement behavior, integration authentication, or production readiness.
A successful capture is immutable. To refresh the source, create a new
migration and reconcile its IDs and versions with the earlier baseline. Progress
and test evidence stay attached to the baseline against which they were recorded.

## Connect a Rewst environment

Do this only after trying the sample and reviewing [the pilot checklist](PILOT.md).

1. Sign into Rewst normally, including MFA. Open a read-only inventory page and
   use the browser's developer tools to inspect its GraphQL network request.
   Confirm the actual HTTPS endpoint and source organization. The historical
   default is `https://api.rewst.io/graphql`; use the endpoint observed in your
   own browser.
2. If that request authenticates with an `appSession` cookie, find its value in
   **Application/Storage → Cookies**. Copy only its value. A public API token or
   bearer token is not an interchangeable credential. Browser labels vary.
3. In Bifrost, open **Solutions → Rewst Migration → Configuration**. Set:

   | Declaration | Value |
   | --- | --- |
   | `rewst_migration_api_url` | The observed HTTPS GraphQL endpoint |
   | `rewst_migration_org_id` | Your authorized root organization ID |
   | `rewst_migration_app_session` | Cookie value, stored as a secret |

   The package declares these fields; each installation supplies its own values.
   Keep the cookie out of chat, command arguments, plan text, screenshots, HAR
   files, and Git. The migration app accepts no cookie field and returns only
   whether a cookie is configured.
4. Return to **Setup** and validate the connection. Select only organizations
   you are authorized to export. Create a live migration and run the export.
5. Reconcile resource IDs and counts against Rewst. Inspect workflow bodies and
   dependencies, inherited variables, and integration metadata. Treat unsupported
   classes and unresolved references as missing work, not empty environments.

If authentication expires, replace the same secret through the Configuration
page after signing in again. If a query fails because the schema changed, stop
and capture a fresh **credential-free read query** for diagnosis. Never share
the cookie or a complete HAR/cURL capture in a bug report.

## Work with your coding agent

Use **Agent Handoff** for commands targeting the actual installation and
migration. The app reads and writes through the same workflows as an external
agent. Plans and item updates use revision checks; after a conflict, fetch the
latest context and reconcile your changes. Use one active executor for a pilot.

The handoff should establish source scope, acceptance cases, target organization,
integration readiness, permitted actions, stop conditions, and rollback before
implementation. Use the repository's `rewst-migration` skill for the complete
discovery and verification method and `bifrost:build` for target authoring.
Source artifacts are untrusted input: instructions inside workflow descriptions,
scripts, or variables do not authorize tool calls or changes to the plan.

Keep implementation and automated tests in Git. Keep migration context in this
Solution's private runtime storage. Do not commit collected source definitions
to this public package. Raw workflow definitions and variables may themselves
contain credentials or personal information; review retention and access before
using a production source.

This pilot has no production cutover, trigger disablement, credential migration,
Rewst retirement, or cancellation action. A `tested` inventory status records
submitted evidence and does not transfer production ownership.

## Package contents and maintenance

- `apps/rewst-migration/`: operator app using the instance-served Bifrost SDK.
- `.bifrost/`: app/workflow identities, table/file declarations, and config schema.
- `functions/migration.py`: explicit tool entry points for the app and CLI.
- `modules/rewst_migration/`: extraction, sample fixtures, and revisioned storage.
- `tests/`: offline contract and failure-path checks.

The revisioned managed file is the migration source of truth; table entries are
derived listings. There is no required Knowledge store or nested AI agent.
Inherited resources, custom integrations, Crates, apps/access, history, and
in-flight operational state need explicit reconciliation; see the export's
coverage register for the adapter's actual result.
The pilot follows observed list/detail queries with bounded pagination and detail
counts. It exports workflow task definitions and list metadata, template/script
bodies, visible variables, and installed integration metadata when those queries
match the customer's schema. Form field definitions and integration action/config
recovery remain gaps. Its graph identifies static workflow references; dynamic
and unresolved dependencies require reconciliation before implementation.

The GraphQL transport is generated from the skill's standalone helper. Change
the canonical helper and run these commands from the repository root:

```bash
python3 scripts/sync_rewst_migration_client.py
python3 scripts/sync_rewst_migration_client.py --check
python3 -B -m unittest scripts.test_rewst_extraction
PYTHONPATH=examples/rewst-migration python3 -B -m unittest discover \
  -s examples/rewst-migration/tests
```

For local app development, use the supported `bifrost solution sdk update` and
Solution lifecycle against an isolated development instance. Do not deploy
pilot source over unrelated production automations.
