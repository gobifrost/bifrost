import { useCallback, useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { agentPlatform } from "@/services/agentPlatform";
import { PlatformError } from "./PlatformEvidence";
import { summarizeExpectation } from "./expectationSummaries";
import type { components } from "@/lib/v1";

type AssertionDef = components["schemas"]["EvaluationAssertion"];

const initialFixture = {
	version: 1,
	entities: {},
	allowed_tools: [],
	rules: [],
};
const initialAssertions: AssertionDef[] = [
	{
		type: "terminal_status",
		label: "Run completes",
		params: { status: "completed" },
	},
	{ type: "no_real_tools", label: "No real tools run", params: {} },
];

const TERMINAL_STATUSES = [
	"completed",
	"failed",
	"cancelled",
	"timeout",
	"contract_failed",
	"error",
	"budget_exceeded",
];

type GuidedKind =
	| "terminal_status"
	| "tool_called"
	| "tool_not_called"
	| "output_path"
	| "max"
	| "no_real_tools";

const GUIDED_TYPES: { value: string; label: string }[] = [
	{ value: "terminal_status", label: "Run finishes with status" },
	{ value: "tool_called", label: "Tool called" },
	{ value: "tool_not_called", label: "Tool not called" },
	{ value: "output_path", label: "Output field check" },
	{ value: "max_iterations", label: "Maximum iterations (observed)" },
	{ value: "max_tokens", label: "Maximum tokens (observed)" },
	{ value: "max_cost_usd", label: "Maximum cost USD (observed)" },
	{ value: "max_latency_ms", label: "Maximum duration ms (observed)" },
	{ value: "no_real_tools", label: "No real tools (safety check)" },
];

const MAX_UNITS: Record<string, string> = {
	max_iterations: "iterations",
	max_tokens: "tokens",
	max_cost_usd: "USD",
	max_latency_ms: "ms",
};

function displayName(def: AssertionDef): string {
	if (typeof def.label === "string" && def.label) return def.label;
	const found = GUIDED_TYPES.find((entry) => entry.value === def.type);
	return found?.label ?? def.type;
}

/** Guided editor key for definitions the controls can represent, else null. */
function guidedKind(def: AssertionDef): GuidedKind | null {
	const params =
		def.params && typeof def.params === "object" ? def.params : null;
	if (!params) return null;
	switch (def.type) {
		case "terminal_status":
			return typeof params.status === "string" ? "terminal_status" : null;
		case "tool_called":
			return typeof params.tool === "string" ? "tool_called" : null;
		case "tool_not_called":
			return typeof params.tool === "string" ? "tool_not_called" : null;
		case "output_path": {
			if (typeof params.path !== "string") return null;
			if ("equals" in params || "contains" in params) {
				if ("contains" in params && typeof params.contains !== "string")
					return null;
			}
			return "output_path";
		}
		case "max_iterations":
		case "max_tokens":
		case "max_cost_usd":
		case "max_latency_ms": {
			const limit = params.limit ?? params.max;
			return typeof limit === "number" ? "max" : null;
		}
		case "no_real_tools":
			return "no_real_tools";
		default:
			return null;
	}
}

function defaultParams(type: string): Record<string, unknown> {
	switch (type) {
		case "terminal_status":
			return { status: "completed" };
		case "tool_called":
		case "tool_not_called":
			return { tool: "" };
		case "output_path":
			return { path: "" };
		case "max_iterations":
		case "max_tokens":
		case "max_cost_usd":
		case "max_latency_ms":
			return { limit: 0 };
		default:
			return {};
	}
}

function defaultLabel(type: string): string {
	return (
		GUIDED_TYPES.find((entry) => entry.value === type)?.label ?? type
	);
}

function parseObject(
	text: string,
): { value: Record<string, unknown> | null; error: string | null } {
	try {
		const parsed: unknown = JSON.parse(text);
		if (!parsed || Array.isArray(parsed) || typeof parsed !== "object")
			return { value: null, error: "Must be a JSON object." };
		return { value: parsed as Record<string, unknown>, error: null };
	} catch {
		return { value: null, error: "Invalid JSON." };
	}
}

/**
 * Structural shape check for one Advanced expectations entry, mirroring the
 * backend EvaluationAssertion DTO (required nonempty string `type`, object
 * `params` when present, string-or-null `label` when present). Unknown types
 * and extra fields are accepted untouched; only the shape is validated so a
 * malformed entry can never reach the renderers.
 */
function assertionShapeError(value: unknown, index: number): string | null {
	const prefix = `Assertion ${index + 1}`;
	if (!value || typeof value !== "object" || Array.isArray(value))
		return `${prefix}: must be an object with a "type".`;
	const def = value as Record<string, unknown>;
	if (typeof def.type !== "string" || !def.type.trim())
		return `${prefix}: "type" must be a nonempty string.`;
	if (
		def.params !== undefined &&
		(!def.params ||
			typeof def.params !== "object" ||
			Array.isArray(def.params))
	)
		return `${prefix}: "params" must be an object when present.`;
	if (
		def.label !== undefined &&
		def.label !== null &&
		typeof def.label !== "string"
	)
		return `${prefix}: "label" must be a string or null when present.`;
	return null;
}

export function CaseEditor({
	suiteId,
	draft,
	onSaved,
	onCancel,
	findingId,
	initialName,
}: {
	suiteId: string;
	draft?: components["schemas"]["EvaluationCasePublic"];
	onSaved: () => void;
	onCancel: () => void;
	/** Reviewed finding this case reproduces (forces finding provenance). */
	findingId?: string;
	/** Editable starting title (e.g. seeded from a finding). */
	initialName?: string;
}) {
	const [name, setName] = useState(draft?.name ?? initialName ?? "");
	const [input, setInput] = useState(
		JSON.stringify(draft?.input ?? { message: "" }, null, 2),
	);
	const [fixture, setFixture] = useState(
		JSON.stringify(draft?.fixture ?? initialFixture, null, 2),
	);
	const [items, setItems] = useState<AssertionDef[]>(() =>
		structuredClone(
			(draft?.assertions ?? initialAssertions) as AssertionDef[],
		),
	);
	const [assertionsText, setAssertionsText] = useState<string | null>(null);
	const [addType, setAddType] = useState("terminal_status");
	const [editingKey, setEditingKey] = useState<number | null>(null);
	/** True while the open guided expectation editor holds an invalid value. */
	const [editInvalid, setEditInvalid] = useState(false);
	const handleEditValidity = useCallback((invalid: boolean) => {
		setEditInvalid(invalid);
	}, []);
	const [repetitions, setRepetitions] = useState(draft?.repetitions ?? 1);
	const [policies, setPolicies] = useState(
		JSON.stringify(
			{
				simulator_policy: draft?.simulator_policy ?? {},
				expected_tools: draft?.expected_tools ?? [],
				forbidden_tools: draft?.forbidden_tools ?? [],
				output_schema: draft?.output_schema ?? null,
				scoring_policy: draft?.scoring_policy ?? {},
				tags: draft?.tags ?? [],
			},
			null,
			2,
		),
	);
	const [tool, setTool] = useState("");
	const [response, setResponse] = useState('{"items": [], "total": 0}');
	const [match, setMatch] = useState("{}");
	const [localError, setLocalError] = useState<unknown>();
	const assertionsInvalid = assertionsText !== null;

	const inputParsed = parseObject(input);
	const inputMessage =
		inputParsed.value && typeof inputParsed.value.message === "string"
			? inputParsed.value.message
			: null;
	function setMessage(message: string) {
		if (!inputParsed.value) return;
		setInput(
			JSON.stringify({ ...inputParsed.value, message }, null, 2),
		);
	}

	const fixtureParsed = parseObject(fixture);
	const fixtureRules = Array.isArray(fixtureParsed.value?.rules)
		? (fixtureParsed.value?.rules as Record<string, unknown>[])
		: null;
	const fixtureAllowed = Array.isArray(fixtureParsed.value?.allowed_tools)
		? (fixtureParsed.value?.allowed_tools as unknown[])
		: null;

	function updateItem(index: number, next: AssertionDef) {
		setItems((previous) =>
			previous.map((item, position) =>
				position === index ? next : item,
			),
		);
	}
	function removeItem(index: number) {
		// While a guided edit holds an invalid value, other rows are locked;
		// removing the row being edited is the explicit discard.
		if (editInvalid && index !== editingKey) return;
		setItems((previous) => previous.filter((_, position) => position !== index));
		if (editingKey === null) return;
		if (index === editingKey) {
			setEditingKey(null);
			setEditInvalid(false);
		} else if (index < editingKey) {
			// Keep the open editor with the same definition after the shift.
			setEditingKey(editingKey - 1);
		}
	}
	function addItem() {
		if (assertionsInvalid || editInvalid) return;
		setItems((previous) => [
			...previous,
			{ type: addType, label: defaultLabel(addType), params: defaultParams(addType) },
		]);
		setEditingKey(items.length);
		setEditInvalid(false);
		setLocalError(undefined);
	}
	function validateItems(list: AssertionDef[]): string | null {
		for (const [position, item] of list.entries()) {
			const params = item.params ?? {};
			const prefix = `Expectation ${position + 1} (${displayName(item)})`;
			if (item.type === "tool_called" || item.type === "tool_not_called") {
				if (
					typeof params.tool !== "string" ||
					!params.tool.trim()
				)
					return `${prefix}: enter a tool name.`;
			}
			if (item.type === "output_path") {
				if (
					typeof params.path !== "string" ||
					!params.path.trim()
				)
					return `${prefix}: enter an output field path.`;
			}
			if (
				item.type === "max_iterations" ||
				item.type === "max_tokens" ||
				item.type === "max_cost_usd" ||
				item.type === "max_latency_ms"
			) {
				const limit = params.limit ?? params.max;
				if (
					typeof limit !== "number" ||
					!Number.isFinite(limit) ||
					limit < 0
				)
					return `${prefix}: enter a number 0 or above (${MAX_UNITS[item.type]}).`;
			}
			if (item.type === "terminal_status") {
				if (
					typeof params.status !== "string" ||
					!params.status.trim()
				)
					return `${prefix}: choose a terminal status.`;
			}
		}
		return null;
	}

	const save = useMutation({
		mutationFn: async () => {
			if (assertionsInvalid)
				throw new Error(
					"Fix the Advanced expectations JSON or revert it before saving.",
				);
			if (editInvalid)
				throw new Error(
					"Fix the highlighted expectation error before saving, or remove that expectation.",
				);
			const options = JSON.parse(policies);
			const body = {
				simulator_policy: options.simulator_policy,
				expected_tools: options.expected_tools,
				forbidden_tools: options.forbidden_tools,
				output_schema: options.output_schema,
				scoring_policy: options.scoring_policy,
				tags: options.tags,
				name,
				input: JSON.parse(input),
				fixture: JSON.parse(fixture),
				assertions: items,
				repetitions,
			};
			if (
				!body.input ||
				Array.isArray(body.input) ||
				typeof body.input !== "object"
			)
				throw new Error("Invocation input must be a JSON object.");
			const problem = validateItems(items);
			if (problem) throw new Error(problem);
			if (draft)
				return agentPlatform.updateCase(suiteId, draft.id, {
					...body,
					expected_version: draft.version,
				});
			return agentPlatform.createCase(suiteId, {
				...body,
				position: 0,
				enabled: true,
				provenance: findingId ? "finding" : "manual",
				finding_id: findingId ?? null,
			});
		},
		onSuccess: onSaved,
	});
	function addRule(failure: boolean) {
		try {
			const next = JSON.parse(fixture);
			if (!tool.trim())
				throw new Error("Enter the exact published tool name first.");
			next.allowed_tools = [
				...new Set([...(next.allowed_tools ?? []), tool.trim()]),
			];
			if (!failure)
				next.rules = [
					...(next.rules ?? []),
					{
						tool: tool.trim(),
						match_args: JSON.parse(match),
						return: JSON.parse(response),
					},
				];
			setFixture(JSON.stringify(next, null, 2));
			setLocalError(undefined);
		} catch (error) {
			setLocalError(error);
		}
	}
	function onAssertionsTextChange(value: string) {
		let parsed: unknown;
		try {
			parsed = JSON.parse(value);
		} catch {
			setAssertionsText(value);
			setLocalError(
				new Error(
					"Expectations JSON is invalid. Fix it or revert to keep editing.",
				),
			);
			return;
		}
		if (!Array.isArray(parsed)) {
			setAssertionsText(value);
			setLocalError(
				new Error(
					"Expectations JSON must be a list. Fix it or revert to keep editing.",
				),
			);
			return;
		}
		for (const [index, entry] of parsed.entries()) {
			const shapeError = assertionShapeError(entry, index);
			if (shapeError) {
				setAssertionsText(value);
				setLocalError(
					new Error(
						`${shapeError} Fix it or revert to keep editing.`,
					),
				);
				return;
			}
		}
		setItems(parsed as AssertionDef[]);
		setAssertionsText(null);
		setLocalError(undefined);
	}
	return (
		<form
			aria-label={draft ? "Edit test" : "Add test"}
			className="space-y-5 rounded-lg border p-4 sm:p-6"
			onSubmit={(event) => {
				event.preventDefault();
				save.mutate();
			}}
		>
			<div>
				<h3 className="text-lg font-semibold">
					{draft ? "Edit test" : "Add a test"}
				</h3>
				<p className="mt-1 text-sm text-muted-foreground">
					{draft
						? "Save changes, inspect the test, then accept it explicitly."
						: "Review the scenario, tool responses and expectations before saving this test. Future runs reuse the exact responses."}
				</p>
			</div>
			<div>
				<Label htmlFor="case-name">Test name</Label>
				<Input
					id="case-name"
					required
					value={name}
					onChange={(event) => setName(event.target.value)}
				/>
			</div>
			<section aria-label="Scenario" className="space-y-3">
				<h4 className="text-sm font-semibold">Scenario</h4>
				{inputMessage !== null ? (
					<div>
						<Label htmlFor="case-message">Message</Label>
						<Textarea
							id="case-message"
							rows={3}
							value={inputMessage}
							onChange={(event) =>
								setMessage(event.target.value)
							}
							placeholder="What should the user ask?"
						/>
					</div>
				) : (
					<p className="text-sm text-muted-foreground">
						This invocation has no message field. Edit the full
						input in Advanced input below.
					</p>
				)}
				<details open={inputParsed.error ? true : undefined}>
					<summary className="cursor-pointer text-sm font-medium">
						Advanced input
					</summary>
					<div className="mt-3">
						<Label htmlFor="case-input">
							Invocation input (JSON)
						</Label>
						<Textarea
							id="case-input"
							rows={4}
							className="font-mono text-xs"
							value={input}
							onChange={(event) => setInput(event.target.value)}
						/>
						{inputParsed.error && (
							<p role="alert" className="mt-1 text-sm text-destructive">
								{inputParsed.error} Your text is preserved.
							</p>
						)}
					</div>
				</details>
			</section>
			<fieldset className="space-y-3 rounded-md border p-4">
				<legend className="px-1 text-sm font-medium">
					Tool responses
				</legend>
				<p className="text-sm text-muted-foreground">
					Use published tool names and schema-valid arguments. Add
					distinct matching responses for empty, nested or failed
					business outcomes. Shared entities and mutations keep
					create/read/update chains coherent.
				</p>
				<div>
					<Label htmlFor="mock-tool">Tool name</Label>
					<Input
						id="mock-tool"
						value={tool}
						onChange={(event) => setTool(event.target.value)}
						placeholder="Exact published tool name"
					/>
				</div>
				<div className="grid gap-3 md:grid-cols-2">
					<div>
						<Label htmlFor="mock-match">
							Match arguments (JSON, dotted paths)
						</Label>
						<Textarea
							id="mock-match"
							className="font-mono text-xs"
							value={match}
							onChange={(event) => setMatch(event.target.value)}
						/>
					</div>
					<div>
						<Label htmlFor="mock-response">
							Returned result (JSON)
						</Label>
						<Textarea
							id="mock-response"
							className="font-mono text-xs"
							value={response}
							onChange={(event) =>
								setResponse(event.target.value)
							}
						/>
					</div>
				</div>
				<div className="flex flex-wrap gap-2">
					<Button
						type="button"
						variant="outline"
						onClick={() => addRule(false)}
					>
						Add response rule
					</Button>
					<Button
						type="button"
						variant="outline"
						onClick={() => addRule(true)}
					>
						Allow tool without a response rule
					</Button>
				</div>
				<p className="text-xs text-muted-foreground">
					A tool with no matching rule or built-in synthetic CRUD
					behavior fails with a controlled tool error. No real tool is
					called. To test this failure, leave that tool unhandled and
					ask the Agent to call it.
				</p>
			</fieldset>
			<section aria-label="Tool responses" className="space-y-3">
				<h4 className="text-sm font-semibold">
					Configured tool responses
				</h4>
				{fixtureParsed.error ? (
					<p role="alert" className="text-sm text-destructive">
						Tool responses JSON is invalid. Your text is preserved
						below.
					</p>
				) : (
					<ul className="space-y-2 text-sm">
						{(fixtureAllowed ?? []).map((toolName, index) => (
							<li key={index}>
								<span className="font-medium">
									{String(toolName)}
								</span>{" "}
								<span className="text-muted-foreground">
									{fixtureRules?.some(
										(rule) => rule.tool === toolName,
									)
										? "responds with a configured result"
										: "fails when called (no response configured)"}
								</span>
							</li>
						))}
						{(fixtureRules ?? [])
							.filter(
								(rule) =>
									!fixtureAllowed?.includes(rule.tool),
							)
							.map((rule, index) => (
								<li key={`rule-${index}`}>
									<span className="font-medium">
										{String(rule.tool ?? "unknown tool")}
									</span>{" "}
									<span className="text-muted-foreground">
										responds to{" "}
										{JSON.stringify(
											rule.match_args ?? {},
										)}
									</span>
								</li>
							))}
						{!fixtureAllowed?.length && !fixtureRules?.length && (
							<li className="text-muted-foreground">
								No tool responses yet. Add a response rule
								above.
							</li>
						)}
					</ul>
				)}
				<details open={fixtureParsed.error ? true : undefined}>
					<summary className="cursor-pointer text-sm font-medium">
						Advanced tool responses (JSON)
					</summary>
					<div className="mt-3">
						<Label htmlFor="case-fixture">
							Complete tool responses (JSON)
						</Label>
						<Textarea
							id="case-fixture"
							rows={10}
							className="max-h-96 font-mono text-xs"
							value={fixture}
							onChange={(event) =>
								setFixture(event.target.value)
							}
						/>
						<p className="mt-1 text-xs text-muted-foreground">
							Rules support tool, match_args, return and
							mutate. Entities map collection names to records
							by ID. Review all rules together before saving.
						</p>
					</div>
				</details>
			</section>
			<section aria-label="Expected behavior" className="space-y-3">
				<h4 className="text-sm font-semibold">Expected behavior</h4>
				<ul className="divide-y rounded-lg border">
					{items.map((item, index) => {
						const kind = guidedKind(item);
						const isEditing = editingKey === index;
						const summary = summarizeExpectation(item);
						return (
							<li key={index} className="space-y-3 p-4">
								<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
									<div className="min-w-0">
										<p className="break-words text-sm font-medium">
											{displayName(item)}
										</p>
										<p className="break-words text-xs text-muted-foreground">
											{summary ??
												(kind
													? item.type
													: "Advanced detail — edit in Advanced expectations below")}
										</p>
									</div>
									<div className="flex flex-wrap gap-2">
										{kind && !isEditing && (
											<Button
												type="button"
												size="sm"
												variant="outline"
												disabled={
													assertionsInvalid || editInvalid
												}
												onClick={() => {
													if (editInvalid) return;
													setEditInvalid(false);
													setEditingKey(index);
												}}
											>
												Edit
											</Button>
										)}
										<Button
											type="button"
											size="sm"
											variant="ghost"
											disabled={
												assertionsInvalid ||
												(editInvalid &&
													editingKey !== index)
											}
											onClick={() => removeItem(index)}
										>
											Remove
										</Button>
									</div>
								</div>
								{isEditing && kind && (
									<AssertionEditor
										key={`${index}:${item.type}`}
										item={item}
										kind={kind}
										onChange={(next) =>
											updateItem(index, next)
										}
										onValidityChange={handleEditValidity}
										onDone={() => {
											setEditInvalid(false);
											setEditingKey(null);
										}}
									/>
								)}
								{!kind && (
									<details>
										<summary className="cursor-pointer text-xs text-muted-foreground">
											Definition (Advanced only)
										</summary>
										<pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 p-3 font-mono text-xs">
											{JSON.stringify(item, null, 2)}
										</pre>
									</details>
								)}
							</li>
						);
					})}
				</ul>
				{editInvalid && (
					<p role="note" className="text-xs text-muted-foreground">
						The open expectation has an invalid value. Correct it,
						or remove that expectation, before editing anything
						else.
					</p>
				)}
				{assertionsInvalid && (
					<div className="space-y-2 rounded-md border p-3">
						<p className="text-sm text-muted-foreground">
							Advanced JSON has an error. Guided editing is
							paused until it is fixed or reverted.
						</p>
						<Button
							type="button"
							variant="outline"
							onClick={() => {
								setAssertionsText(null);
								setLocalError(undefined);
							}}
						>
							Revert to last valid
						</Button>
					</div>
				)}
				<div className="flex flex-wrap items-end gap-2">
					<div className="min-w-40 flex-1">
						<Label htmlFor="add-expectation">
							Add expectation
						</Label>
						<select
							id="add-expectation"
							className="h-10 w-full rounded-md border bg-background px-3 text-sm"
							value={addType}
							onChange={(event) =>
								setAddType(event.target.value)
							}
							disabled={assertionsInvalid || editInvalid}
						>
							{GUIDED_TYPES.map((entry) => (
								<option key={entry.value} value={entry.value}>
									{entry.label}
								</option>
							))}
						</select>
					</div>
					<Button
						type="button"
						variant="outline"
						disabled={assertionsInvalid || editInvalid}
						onClick={addItem}
					>
						Add
					</Button>
				</div>
				<p className="text-xs text-muted-foreground">
					Maximums are observed expectations about a finished run,
					not spend caps. Less common checks stay available in
					Advanced expectations below.
				</p>
				<details>
					<summary className="cursor-pointer text-sm font-medium">
						Advanced expectations (JSON)
					</summary>
					<div className="mt-3">
						<Label htmlFor="case-assertions">
							Expectations (JSON)
						</Label>
						<Textarea
							id="case-assertions"
							rows={7}
							className="font-mono text-xs"
							disabled={editInvalid}
							value={
								assertionsText ??
								JSON.stringify(items, null, 2)
							}
							onChange={(event) =>
								onAssertionsTextChange(event.target.value)
							}
						/>
					</div>
				</details>
			</section>
			<details>
				<summary className="cursor-pointer text-sm font-medium">
					Tool expectations, output schema and policies
				</summary>
				<div className="mt-3">
					<Label htmlFor="case-policies">Case policies (JSON)</Label>
					<Textarea
						id="case-policies"
						rows={8}
						className="font-mono text-xs"
						value={policies}
						onChange={(event) => setPolicies(event.target.value)}
					/>
				</div>
			</details>
			<div className="max-w-40">
				<Label htmlFor="case-repetitions">Repetitions</Label>
				<Input
					id="case-repetitions"
					type="number"
					min={1}
					max={10}
					required
					value={repetitions}
					onChange={(event) =>
						setRepetitions(Number(event.target.value))
					}
				/>
			</div>
			<PlatformError error={localError ?? save.error} />
			<div className="flex gap-2">
				<Button disabled={save.isPending}>
					{save.isPending
						? "Saving…"
						: draft
							? "Save test changes"
							: "Save test"}
				</Button>
				<Button type="button" variant="outline" onClick={onCancel}>
					Cancel
				</Button>
			</div>
		</form>
	);
}

type ValueKind = "text" | "number" | "true" | "false" | "null" | "json";

function inferKindAndText(value: unknown): [ValueKind, string] {
	if (typeof value === "number" && Number.isFinite(value))
		return ["number", String(value)];
	if (value === true) return ["true", ""];
	if (value === false) return ["false", ""];
	if (value === null || value === undefined) return ["null", ""];
	if (typeof value === "string") return ["text", value];
	try {
		return ["json", JSON.stringify(value)];
	} catch {
		return ["json", ""];
	}
}

function parseTypedValue(
	kind: ValueKind,
	text: string,
): { value: unknown; error: string | null } {
	switch (kind) {
		case "number": {
			if (!text.trim()) return { value: undefined, error: "Enter a value." };
			const parsed = Number(text);
			if (!Number.isFinite(parsed))
				return { value: undefined, error: "Enter a finite number." };
			return { value: parsed, error: null };
		}
		case "true":
			return { value: true, error: null };
		case "false":
			return { value: false, error: null };
		case "null":
			return { value: null, error: null };
		case "json": {
			try {
				return { value: JSON.parse(text), error: null };
			} catch {
				return { value: undefined, error: "Invalid JSON value." };
			}
		}
		default:
			return { value: text, error: null };
	}
}

function AssertionEditor({
	item,
	kind,
	onChange,
	onDone,
	onValidityChange,
}: {
	item: AssertionDef;
	kind: Exclude<GuidedKind, null>;
	onChange: (next: AssertionDef) => void;
	onDone: () => void;
	onValidityChange?: (invalid: boolean) => void;
}) {
	const params = item.params ?? {};
	const [editorError, setEditorError] = useState<string | null>(null);
	useEffect(() => {
		onValidityChange?.(editorError !== null);
	}, [editorError, onValidityChange]);
	function setParams(next: Record<string, unknown>) {
		onChange({ ...item, params: next });
	}
	function setLabel(label: string) {
		onChange({ ...item, label });
	}
	const outputMode =
		"equals" in params ? "equals" : "contains" in params ? "contains" : "present";
	const [valueKind, setValueKind] = useState<ValueKind>(
		() =>
			inferKindAndText(
				outputMode === "present" ? undefined : params[outputMode],
			)[0],
	);
	const [valueText, setValueText] = useState(
		() =>
			inferKindAndText(
				outputMode === "present" ? undefined : params[outputMode],
			)[1],
	);
	function commitValue(
		kind: ValueKind,
		text: string,
		mode: string,
		base: Record<string, unknown>,
	) {
		const next = { ...base };
		delete next.equals;
		delete next.contains;
		if (mode === "present") {
			setEditorError(null);
			setParams(next);
			return;
		}
		const parsed = parseTypedValue(kind, text);
		if (parsed.error) {
			setEditorError(parsed.error);
			return;
		}
		setEditorError(null);
		next[mode] = parsed.value;
		setParams(next);
	}
	const maxType = item.type.startsWith("max_") ? item.type : null;
	return (
		<div className="space-y-3 rounded-md border p-3">
			<div>
				<Label htmlFor={`assertion-label-${item.type}`}>Label</Label>
				<Input
					id={`assertion-label-${item.type}`}
					value={typeof item.label === "string" ? item.label : ""}
					onChange={(event) => setLabel(event.target.value)}
					placeholder="Short name for this check"
				/>
			</div>
			{kind === "terminal_status" && (
				<div>
					<Label htmlFor="assertion-status">Terminal status</Label>
					<select
						id="assertion-status"
						className="h-10 w-full rounded-md border bg-background px-3 text-sm"
						value={String(params.status ?? "completed")}
						onChange={(event) =>
							setParams({ ...params, status: event.target.value })
						}
					>
						{TERMINAL_STATUSES.map((status) => (
							<option key={status} value={status}>
								{status}
							</option>
						))}
					</select>
				</div>
			)}
			{(kind === "tool_called" || kind === "tool_not_called") && (
				<div>
					<Label htmlFor="assertion-tool">Expected tool</Label>
					<Input
						id="assertion-tool"
						value={String(params.tool ?? "")}
						onChange={(event) =>
							setParams({ ...params, tool: event.target.value })
						}
						placeholder="Exact published tool name"
					/>
				</div>
			)}
			{kind === "output_path" && (
				<div className="space-y-3">
					<div>
						<Label htmlFor="assertion-path">Output field path</Label>
						<Input
							id="assertion-path"
							value={String(params.path ?? "")}
							onChange={(event) =>
								setParams({ ...params, path: event.target.value })
							}
							placeholder="answer.text"
						/>
					</div>
					<div>
						<Label htmlFor="assertion-mode">Check</Label>
						<select
							id="assertion-mode"
							className="h-10 w-full rounded-md border bg-background px-3 text-sm"
							value={outputMode}
							onChange={(event) => {
								commitValue(
									valueKind,
									valueText,
									event.target.value,
									params,
								);
							}}
						>
							<option value="present">Field is present</option>
							<option value="equals">Equals a value</option>
							<option value="contains">Contains a value</option>
						</select>
					</div>
					{outputMode !== "present" && (
						<div className="grid gap-3 sm:grid-cols-2">
							<div>
								<Label htmlFor="assertion-value-kind">
									Value type
								</Label>
								<select
									id="assertion-value-kind"
									className="h-10 w-full rounded-md border bg-background px-3 text-sm"
									value={valueKind}
									onChange={(event) => {
										const kind =
											event.target.value as ValueKind;
										setValueKind(kind);
										commitValue(
											kind,
											valueText,
											outputMode,
											params,
										);
									}}
								>
									<option value="text">Text</option>
									<option value="number">Number</option>
									<option value="true">True</option>
									<option value="false">False</option>
									<option value="null">Null</option>
									<option value="json">JSON</option>
								</select>
							</div>
							<div>
								<Label htmlFor="assertion-value">Value</Label>
								{valueKind === "text" ||
								valueKind === "number" ||
								valueKind === "json" ? (
									<Input
										id="assertion-value"
										value={valueText}
										onChange={(event) => {
											const text = event.target.value;
											setValueText(text);
											commitValue(
												valueKind,
												text,
												outputMode,
												params,
											);
										}}
									/>
								) : (
									<p className="flex min-h-10 items-center text-sm text-muted-foreground">
										Uses the {valueKind} literal.
									</p>
								)}
							</div>
						</div>
					)}
				</div>
			)}
			{maxType && (
				<div>
					<Label htmlFor="assertion-limit">
						Limit ({MAX_UNITS[maxType]})
					</Label>
					<Input
						id="assertion-limit"
						type="number"
						min={0}
						value={String(
							(params.limit ?? params.max ?? "") as
								| string
								| number,
						)}
						onChange={(event) => {
							const parsed = Number(event.target.value);
							if (
								event.target.value !== "" &&
								(!Number.isFinite(parsed) || parsed < 0)
							) {
								setEditorError(
									`Enter a number 0 or above (${MAX_UNITS[maxType]}).`,
								);
								return;
							}
							setEditorError(null);
							setParams({
								...params,
								limit:
									event.target.value === ""
										? undefined
										: parsed,
							});
						}}
					/>
					<p className="mt-1 text-xs text-muted-foreground">
						Observed after the run finishes; not a spend cap.
					</p>
				</div>
			)}
			{kind === "no_real_tools" && (
				<p className="text-sm text-muted-foreground">
					Safety check: the run must execute zero real tools. No
					settings needed.
				</p>
			)}
			{editorError && (
				<p role="alert" className="text-sm text-destructive">
					{editorError}
				</p>
			)}
			<Button
				type="button"
				size="sm"
				variant="outline"
				disabled={editorError !== null}
				onClick={onDone}
			>
				Done
			</Button>
		</div>
	);
}
