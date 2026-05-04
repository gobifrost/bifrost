/**
 * Side panel that documents the policy AST: USER fields, ROW field examples,
 * available functions, operators, copy-pasteable worked examples, and
 * footguns. Lives inside the PolicyEditor and is toggled via the "Reference"
 * button.
 *
 * Sourced from `api/src/models/contracts/policies.py` (KNOWN_USER_FIELDS,
 * _ALL_OPS), `api/shared/policies/functions.py` (FUNCTIONS), and the worked
 * scenarios in docs/superpowers/specs/2026-04-30-table-policies-design.md.
 * Keep this in sync if those constants change.
 */

import { useState } from "react";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { CodeEditor } from "./CodeEditor";
import { POLICY_TEMPLATES } from "./policy-templates";
import type { components } from "@/lib/v1";

type TablePolicies = components["schemas"]["TablePolicies"];

interface RefRow {
	term: string;
	def: string;
}

const USER_FIELDS: RefRow[] = [
	{ term: "user_id", def: "UUID of the calling user." },
	{ term: "email", def: "Email address of the calling user." },
	{ term: "organization_id", def: "Org UUID the user belongs to." },
	{ term: "is_platform_admin", def: "Boolean. True for platform admins." },
	{ term: "role_ids", def: "List of role UUIDs the user holds." },
	{ term: "role_names", def: "List of role names the user holds." },
];

const ROW_FIELDS: RefRow[] = [
	{ term: "id", def: "Document UUID." },
	{ term: "created_by", def: "UUID of the user who created the row." },
	{
		term: "organization_id",
		def: "Org UUID of the row (set when the row is org-scoped).",
	},
	{
		term: "data.<field>",
		def: "Any nested field in the document body, e.g. `data.status`, `data.priority`.",
	},
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
	{
		term: "lt / lte / gt / gte",
		def: "Numeric / lexical comparison. [left, right].",
	},
	{ term: "in", def: "Membership. [operand, [literal, ...]]." },
	{ term: "is_null", def: "True when the operand is null. Single operand." },
	{ term: "call", def: "Invoke a function. { call: name, args: [...] }." },
];

interface WorkedExample {
	heading: string;
	description: string;
	policy: TablePolicies;
}

/**
 * Worked examples — copy-pasteable full policies covering every operator and
 * the canonical scenarios from the table-policies design doc. The first four
 * (admin_bypass, own_row, own_org, role_gated_read) are pulled from
 * `policy-templates.ts` so the "Insert template..." menu and this panel stay
 * aligned.
 *
 * Each `policy` is wrapped in the full `{policies: [...]}` document so Copy →
 * paste-into-fresh-JSON works directly without the user having to add the
 * wrapper themselves.
 */
const EXAMPLES: WorkedExample[] = [
	{
		heading: "admin_bypass",
		description: "Platform admins can do anything.",
		policy: { policies: [POLICY_TEMPLATES.admin_bypass!] },
	},
	{
		heading: "own_row",
		description: "Row owner can read/update/delete.",
		policy: { policies: [POLICY_TEMPLATES.own_row!] },
	},
	{
		heading: "own_org",
		description: "Caller can see rows in their own org.",
		policy: { policies: [POLICY_TEMPLATES.own_org!] },
	},
	{
		heading: "role_gated_read",
		description: "A specific role can read.",
		policy: { policies: [POLICY_TEMPLATES.role_gated_read!] },
	},
	{
		heading: "read_only_finalized",
		description:
			"Anyone in org can read finalized rows; nobody can update.",
		policy: {
			policies: [
				{
					name: "read_only_finalized",
					description:
						"Anyone in org can read finalized rows; nobody can update",
					actions: ["read"],
					when: {
						and: [
							{
								eq: [
									{ row: "organization_id" },
									{ user: "organization_id" },
								],
							},
							{ eq: [{ row: "data.status" }, "finalized"] },
						],
					},
				},
			],
		},
	},
	{
		heading: "range_read",
		description: "Range comparison example.",
		policy: {
			policies: [
				{
					name: "range_read",
					description:
						"Read rows whose data.amount is between 100 and 1000",
					actions: ["read"],
					when: {
						and: [
							{ gte: [{ row: "data.amount" }, 100] },
							{ lte: [{ row: "data.amount" }, 1000] },
						],
					},
				},
			],
		},
	},
	{
		heading: "status_membership",
		description: "Membership against literal list.",
		policy: {
			policies: [
				{
					name: "status_membership",
					description:
						"Read rows whose data.status is one of an allowed list",
					actions: ["read"],
					when: {
						in: [
							{ row: "data.status" },
							["active", "pending", "approved"],
						],
					},
				},
			],
		},
	},
	{
		heading: "unassigned",
		description: "Null check.",
		policy: {
			policies: [
				{
					name: "unassigned",
					description: "Read rows where data.assignee_id is unset",
					actions: ["read"],
					when: { is_null: { row: "data.assignee_id" } },
				},
			],
		},
	},
	{
		heading: "assigned",
		description: "\"Is set\" idiom (not + is_null).",
		policy: {
			policies: [
				{
					name: "assigned",
					description:
						"Read rows where data.assignee_id is set (not null)",
					actions: ["read"],
					when: { not: { is_null: { row: "data.assignee_id" } } },
				},
			],
		},
	},
	{
		heading: "own_open_row",
		description: "Two clauses combined.",
		policy: {
			policies: [
				{
					name: "own_open_row",
					description:
						"Owner can update only while row.data.status is 'open'",
					actions: ["update"],
					when: {
						and: [
							{ eq: [{ row: "created_by" }, { user: "user_id" }] },
							{ eq: [{ row: "data.status" }, "open"] },
						],
					},
				},
			],
		},
	},
	{
		heading: "owner_or_role",
		description: "Alternative grants.",
		policy: {
			policies: [
				{
					name: "owner_or_role",
					description:
						"Either the row owner, or members of 'editors' role can update",
					actions: ["update"],
					when: {
						or: [
							{ eq: [{ row: "created_by" }, { user: "user_id" }] },
							{ call: "has_role", args: ["editors"] },
						],
					},
				},
			],
		},
	},
	{
		heading: "nested_grant",
		description: "Showing precedence + indentation.",
		policy: {
			policies: [
				{
					name: "nested_grant",
					description: "Read if (own row AND open) OR (platform admin)",
					actions: ["read"],
					when: {
						or: [
							{
								and: [
									{
										eq: [
											{ row: "created_by" },
											{ user: "user_id" },
										],
									},
									{ eq: [{ row: "data.status" }, "open"] },
								],
							},
							{ user: "is_platform_admin" },
						],
					},
				},
			],
		},
	},
	{
		heading: "managers_only",
		description: "Function call with role name.",
		policy: {
			policies: [
				{
					name: "managers_only",
					description: "Members of role 'managers' can read all rows",
					actions: ["read"],
					when: { call: "has_role", args: ["managers"] },
				},
			],
		},
	},
	{
		heading: "by_role_uuid",
		description: "Function call with role UUID (string compared).",
		policy: {
			policies: [
				{
					name: "by_role_uuid",
					description: "Specific role UUID can read",
					actions: ["read"],
					when: {
						call: "has_role",
						args: ["00000000-0000-0000-0000-000000000123"],
					},
				},
			],
		},
	},
	{
		heading: "manager_reads_reports",
		description:
			"Denormalized manager_user_id row field (lifted from the design doc).",
		policy: {
			policies: [
				{
					name: "manager_reads_reports",
					description:
						"Manager (denormalized on the row) can read their reports' rows",
					actions: ["read"],
					when: {
						eq: [
							{ row: "data.manager_user_id" },
							{ user: "user_id" },
						],
					},
				},
			],
		},
	},
	{
		heading: "provider_read",
		description:
			"`or` between own-org and platform-admin (cross-org provider scenario).",
		policy: {
			policies: [
				{
					name: "provider_read",
					description:
						"Caller can read if it's their own org's row, or if they're a platform admin (cross-org provider scenario)",
					actions: ["read"],
					when: {
						or: [
							{
								eq: [
									{ row: "organization_id" },
									{ user: "organization_id" },
								],
							},
							{ user: "is_platform_admin" },
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
		title: "null in eq is invalid.",
		body: "`eq` and `neq` reject `null` literals (the validator uses different SQL/evaluator semantics around null). Use `is_null` instead.",
	},
	{
		title: "Validator rejects `eq: [..., null]` literal.",
		body: "Same as above from the API author's perspective — the request will fail validation. Always reach for `is_null` when you mean \"unset\".",
	},
	{
		title: "Empty `in` lists rejected.",
		body: "An `in` predicate requires a non-empty literal list. Empty lists fail validation.",
	},
	{
		title: "`not + is_null` is the \"is set\" idiom.",
		body: "To check that a field is set (non-null), use `{not: {is_null: {row: '...'}}}`.",
	},
	{
		title: "`eq` on missing JSONB path is false, not error.",
		body: "If `data.path` doesn't exist on the row, `eq` returns false; the policy doesn't fail. Use `is_null` if 'unset' should grant access.",
	},
];

function RefSection({ title, rows }: { title: string; rows: RefRow[] }) {
	return (
		<section className="space-y-2">
			<h4 className="text-sm font-semibold">{title}</h4>
			<dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
				{rows.map((r) => (
					<div key={r.term} className="contents">
						<dt className="font-mono text-xs pt-0.5 text-muted-foreground">
							{r.term}
						</dt>
						<dd>{r.def}</dd>
					</div>
				))}
			</dl>
		</section>
	);
}

function ExampleBlock({
	example,
	index,
	copied,
	onCopy,
}: {
	example: WorkedExample;
	index: number;
	copied: boolean;
	onCopy: () => void;
}) {
	const json = JSON.stringify(example.policy, null, 2);
	return (
		<div className="space-y-1">
			<div className="flex items-center justify-between gap-2">
				<h5 className="text-sm font-semibold font-mono">
					{example.heading}
				</h5>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className="h-6 px-2 text-xs"
					onClick={onCopy}
				>
					{copied ? "Copied!" : "Copy"}
				</Button>
			</div>
			<p className="text-xs text-muted-foreground">
				{example.description}
			</p>
			<CodeEditor
				mode="json"
				text={json}
				onChange={() => {}}
				path={`example-${index}.json`}
				height="180px"
				readOnly
			/>
		</div>
	);
}

function ExamplesSection({ examples }: { examples: WorkedExample[] }) {
	const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

	function handleCopy(idx: number, policy: TablePolicies) {
		const text = JSON.stringify(policy, null, 2);
		// Guard the clipboard call so jsdom (which omits navigator.clipboard)
		// doesn't blow up the visual feedback. The button still flips to
		// "Copied!" so users get immediate confirmation either way.
		try {
			void navigator.clipboard?.writeText(text);
		} catch {
			// no-op; the user-visible state still updates
		}
		setCopiedIdx(idx);
		setTimeout(() => {
			setCopiedIdx((current) => (current === idx ? null : current));
		}, 1500);
	}

	return (
		<section className="space-y-3">
			<h4 className="text-sm font-semibold">Worked examples</h4>
			<p className="text-xs text-muted-foreground">
				Copy-pasteable full policies covering every operator and the
				canonical scenarios from the table-policies design doc.
			</p>
			<div className="space-y-4">
				{examples.map((ex, idx) => (
					<ExampleBlock
						key={ex.heading}
						example={ex}
						index={idx}
						copied={copiedIdx === idx}
						onCopy={() => handleCopy(idx, ex.policy)}
					/>
				))}
			</div>
		</section>
	);
}

function FootgunsSection({ entries }: { entries: FootgunEntry[] }) {
	return (
		<section className="space-y-2">
			<h4 className="text-sm font-semibold">Footguns</h4>
			<dl className="space-y-2 text-sm">
				{entries.map((f) => (
					<div key={f.title}>
						<dt className="font-semibold">{f.title}</dt>
						<dd className="text-muted-foreground">{f.body}</dd>
					</div>
				))}
			</dl>
		</section>
	);
}

export interface PolicyReferencePanelProps {
	open: boolean;
	onClose: () => void;
}

export function PolicyReferencePanel({
	open,
	onClose,
}: PolicyReferencePanelProps) {
	return (
		<Sheet open={open} onOpenChange={(v) => !v && onClose()}>
			<SheetContent
				side="right"
				className="w-[420px] sm:w-[480px] overflow-y-auto"
				aria-label="Policy reference"
			>
				<SheetHeader>
					<SheetTitle>Policy reference</SheetTitle>
					<SheetDescription>
						Building blocks for the <code>when</code> expression.
						Each policy is a JSON expression evaluated per row;
						true means the action is allowed.
					</SheetDescription>
				</SheetHeader>
				<div className="space-y-6 px-4 pb-6">
					<RefSection title="USER fields" rows={USER_FIELDS} />
					<RefSection title="ROW fields" rows={ROW_FIELDS} />
					<RefSection title="Functions" rows={FUNCTIONS} />
					<RefSection title="Operators" rows={OPERATORS} />
					<ExamplesSection examples={EXAMPLES} />
					<FootgunsSection entries={FOOTGUNS} />
				</div>
			</SheetContent>
		</Sheet>
	);
}
