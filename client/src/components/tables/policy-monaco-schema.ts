/**
 * Monaco JSON schema registration for the policy editor.
 *
 * Extracted from the (now-removed) PolicyEditorRow so the schema URI and
 * the `configureMonacoSchema` helper live next to each other.
 *
 * Scope note: `policy-schema.json` describes the SHAPE of `Expr` (the
 * `when` AST), not the top-level `TablePolicies` document. We register it
 * under the URI below as a partial validation hint — Monaco will surface
 * errors inside `when` clauses but will NOT catch top-level shape errors
 * (missing `policies` array, wrong root type, etc.). Top-level validation
 * happens at parse time in PolicyEditor (see `asTablePolicies`) and
 * authoritatively on the server. We keep the schema scope limited because
 * inlining a full TablePolicies wrapper here would duplicate the runtime
 * parser's contract and drift from it; the parser is the source of truth.
 *
 * YAML-side note: `monaco-yaml` is NOT in `package.json`, so the YAML tab
 * does not get any schema-driven validation. PolicyCodeView documents this
 * inline.
 */

import type * as Monaco from "monaco-editor";

import schema from "@/lib/app-sdk/policy-schema.json";

const POLICY_SCHEMA_URI = "inmemory://policy-schema.json";

interface MonacoJsonDefaults {
	diagnosticsOptions: {
		schemas?: { uri: string; fileMatch?: string[]; schema: unknown }[];
	};
	setDiagnosticsOptions: (options: {
		validate?: boolean;
		allowComments?: boolean;
		schemas?: { uri: string; fileMatch?: string[]; schema: unknown }[];
	}) => void;
}

/**
 * Register the policy `Expr` schema with Monaco's JSON language service.
 * Idempotent — repeated calls (e.g. from multiple editor mounts) won't
 * stack duplicate schema entries.
 */
export function configureMonacoSchema(monaco: typeof Monaco) {
	// monaco.languages.json is marked deprecated in the type defs but is the
	// runtime API for JSON language features. Cast through to access it.
	const json = (
		monaco.languages as unknown as { json: { jsonDefaults: MonacoJsonDefaults } }
	).json;
	const existing = json.jsonDefaults.diagnosticsOptions.schemas ?? [];
	if (existing.some((s) => s.uri === POLICY_SCHEMA_URI)) return;
	json.jsonDefaults.setDiagnosticsOptions({
		validate: true,
		allowComments: false,
		schemas: [
			...existing,
			{
				uri: POLICY_SCHEMA_URI,
				fileMatch: ["*"],
				schema,
			},
		],
	});
}
