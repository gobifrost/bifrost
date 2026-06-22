/**
 * Reference slideout for file policies — mirrors the Tables PolicyReferencePanel
 * but documents the file action vocabulary (read/write/delete/list) and the
 * `{file: …}` reference namespace (location, path, created_by, created_at).
 *
 * Sourced from `api/src/models/contracts/policies.py` (KNOWN_USER_FIELDS,
 * FileExpr, _ALL_OPS) and `api/shared/file_policies.py` (FilePolicyContext).
 */

import { HelpSlideout } from "@/components/shared/HelpSlideout";
import { PolicyExampleBlock } from "@/components/shared/PolicyExampleBlock";
import type { FilePolicies } from "@/services/filePolicies";

interface RefRow {
	term: string;
	def: string;
}

const ACTIONS: RefRow[] = [
	{ term: "read", def: "Read a file's contents." },
	{ term: "write", def: "Create or overwrite a file (and signed uploads)." },
	{ term: "delete", def: "Delete a file." },
	{ term: "list", def: "List files under a prefix." },
];

const USER_FIELDS: RefRow[] = [
	{ term: "user_id", def: "UUID of the calling user." },
	{ term: "email", def: "Email address of the calling user." },
	{ term: "organization_id", def: "Org UUID the user belongs to." },
	{ term: "is_platform_admin", def: "Boolean. True for platform admins." },
	{ term: "role_ids", def: "List of role UUIDs the user holds." },
	{ term: "role_names", def: "List of role names the user holds." },
];

const FILE_FIELDS: RefRow[] = [
	{ term: "location", def: "The share/location, e.g. `gallery`, `reports`." },
	{ term: "path", def: "File path within the location (no scope segment)." },
	{ term: "created_by", def: "UUID of the user who uploaded the file." },
	{ term: "created_at", def: "Upload timestamp (ISO 8601)." },
];

const FUNCTIONS: RefRow[] = [
	{
		term: "has_role(role_name)",
		def: "True when the calling user has a role whose name matches the literal string argument.",
	},
];

const OPERATORS: RefRow[] = [
	{ term: "and", def: "Logical AND. Array of 2+ operands." },
	{ term: "or", def: "Logical OR. Array of 2+ operands." },
	{ term: "not", def: "Logical NOT. Single operand." },
	{ term: "eq", def: "Equality. [left, right]." },
	{ term: "neq", def: "Inequality. [left, right]." },
	{ term: "in", def: "Membership. [operand, [literal, ...]]." },
	{ term: "is_null", def: "True when the operand is null. Single operand." },
	{ term: "call", def: "Invoke a function. { call: name, args: [...] }." },
];

interface WorkedExample {
	heading: string;
	description: string;
	policy: FilePolicies;
}

const EXAMPLES: WorkedExample[] = [
	{
		heading: "admin_bypass",
		description: "Platform admins can do anything.",
		policy: {
			policies: [
				{
					name: "admin_bypass",
					description: "Platform admins bypass all checks.",
					actions: ["read", "write", "delete", "list"],
					when: { user: "is_platform_admin" },
				},
			],
		},
	},
	{
		heading: "everyone_read",
		description: "No `when` → applies to every authenticated user.",
		policy: {
			policies: [
				{
					name: "everyone_read",
					description: "Anyone may read and list.",
					actions: ["read", "list"],
					when: null,
				},
			],
		},
	},
	{
		heading: "own_files",
		description: "The uploader governs their own files via `{file: created_by}`.",
		policy: {
			policies: [
				{
					name: "own_files",
					description: "Uploader can read/write/delete their files.",
					actions: ["read", "write", "delete"],
					when: { eq: [{ file: "created_by" }, { user: "user_id" }] },
				},
			],
		},
	},
	{
		heading: "role_gated_read",
		description: "Restrict reads to a named role.",
		policy: {
			policies: [
				{
					name: "role_gated_read",
					description: "Only 'analysts' can read.",
					actions: ["read", "list"],
					when: { call: "has_role", args: ["analysts"] },
				},
			],
		},
	},
	{
		heading: "owner_or_role",
		description: "Alternative grants with `or`.",
		policy: {
			policies: [
				{
					name: "owner_or_role",
					description: "The uploader, or members of 'editors', can write.",
					actions: ["write", "delete"],
					when: {
						or: [
							{ eq: [{ file: "created_by" }, { user: "user_id" }] },
							{ call: "has_role", args: ["editors"] },
						],
					},
				},
			],
		},
	},
	{
		heading: "prefix_scoped",
		description: "Combine clauses — read only within a location.",
		policy: {
			policies: [
				{
					name: "reports_readers",
					description: "Members of 'finance' read the reports location.",
					actions: ["read", "list"],
					when: {
						and: [
							{ eq: [{ file: "location" }, "reports"] },
							{ call: "has_role", args: ["finance"] },
						],
					},
				},
			],
		},
	},
];

interface FootgunEntry {
	title: string;
	body: string;
}

const FOOTGUNS: FootgunEntry[] = [
	{
		title: "Default deny.",
		body: "If no policy rule grants an action, it's denied. A path with no policy at all denies everyone (except via a seeded admin_bypass).",
	},
	{
		title: "Deleting admin_bypass locks admins out.",
		body: "The seeded admin_bypass rule is the only admin escape hatch — remove it and platform admins are governed by the remaining rules like anyone else.",
	},
	{
		title: "`null` in eq is invalid.",
		body: "`eq`/`neq` reject `null` literals. Use `is_null` to test for unset, e.g. `{is_null: {file: 'created_by'}}`.",
	},
	{
		title: "Unknown file fields resolve to null.",
		body: "An unknown `{file: ...}` field evaluates to null (fail-closed), so typos quietly deny rather than widen access.",
	},
];

function RefTable({ title, rows }: { title: string; rows: RefRow[] }) {
	return (
		<section className="space-y-2">
			<h4 className="text-sm font-semibold">{title}</h4>
			<dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
				{rows.map((row) => (
					<div key={row.term} className="contents">
						<dt className="font-mono text-foreground">{row.term}</dt>
						<dd className="text-muted-foreground">{row.def}</dd>
					</div>
				))}
			</dl>
		</section>
	);
}

function ExamplesSection() {
	return (
		<section className="space-y-3">
			<h4 className="text-sm font-semibold">Worked examples</h4>
			{EXAMPLES.map((example, index) => (
				<PolicyExampleBlock
					key={example.heading}
					heading={example.heading}
					description={example.description}
					policy={example.policy}
					index={index}
				/>
			))}
		</section>
	);
}

export function FilePolicyReferencePanel() {
	return (
		<HelpSlideout title="File policy reference">
			<RefTable title="Actions" rows={ACTIONS} />
			<RefTable title="User fields ({user: …})" rows={USER_FIELDS} />
			<RefTable title="File fields ({file: …})" rows={FILE_FIELDS} />
			<RefTable title="Functions" rows={FUNCTIONS} />
			<RefTable title="Operators" rows={OPERATORS} />
			<ExamplesSection />
			<section className="space-y-2">
				<h4 className="text-sm font-semibold">Footguns</h4>
				{FOOTGUNS.map((f) => (
					<div key={f.title} className="text-xs">
						<p className="font-medium text-foreground">{f.title}</p>
						<p className="text-muted-foreground">{f.body}</p>
					</div>
				))}
			</section>
		</HelpSlideout>
	);
}
