import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { agentPlatform } from "@/services/agentPlatform";
import { PlatformError } from "./PlatformEvidence";
import type { components } from "@/lib/v1";

const initialFixture = {
	version: 1,
	entities: {},
	allowed_tools: [],
	rules: [],
};
const initialAssertions = [
	{ type: "terminal_status", params: { status: "completed" } },
	{ type: "no_real_tools", params: {} },
];
export function CaseEditor({
	suiteId,
	draft,
	onSaved,
	onCancel,
}: {
	suiteId: string;
	draft?: components["schemas"]["EvaluationCasePublic"];
	onSaved: () => void;
	onCancel: () => void;
}) {
	const [name, setName] = useState(draft?.name ?? "");
	const [input, setInput] = useState(
		JSON.stringify(draft?.input ?? { message: "" }, null, 2),
	);
	const [fixture, setFixture] = useState(
		JSON.stringify(draft?.fixture ?? initialFixture, null, 2),
	);
	const [assertions, setAssertions] = useState(
		JSON.stringify(draft?.assertions ?? initialAssertions, null, 2),
	);
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
	const save = useMutation({
		mutationFn: async () => {
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
				assertions: JSON.parse(assertions),
				repetitions,
			};
			if (
				!body.input ||
				Array.isArray(body.input) ||
				typeof body.input !== "object"
			)
				throw new Error("Invocation input must be a JSON object.");
			if (draft)
				return agentPlatform.updateCase(suiteId, draft.id, {
					...body,
					expected_version: draft.version,
				});
			return agentPlatform.createCase(suiteId, {
				...body,
				position: 0,
				enabled: true,
				provenance: "manual",
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
	return (
		<form
			aria-label={draft ? "Edit draft case" : "Author case"}
			className="space-y-5 rounded-lg border p-4 sm:p-6"
			onSubmit={(event) => {
				event.preventDefault();
				save.mutate();
			}}
		>
			<div>
				<h3 className="text-lg font-semibold">
					{draft ? "Edit review draft" : "Author a regression case"}
				</h3>
				<p className="mt-1 text-sm text-muted-foreground">
					{draft
						? "Save changes, inspect the draft, then accept it explicitly."
						: "Review the invocation, mock responses and assertions before freezing this case. Future runs reuse the exact fixture."}
				</p>
			</div>
			<div>
				<Label htmlFor="case-name">Case name</Label>
				<Input
					id="case-name"
					required
					value={name}
					onChange={(event) => setName(event.target.value)}
				/>
			</div>
			<div>
				<Label htmlFor="case-input">Invocation input (JSON)</Label>
				<Textarea
					id="case-input"
					rows={4}
					className="font-mono text-xs"
					value={input}
					onChange={(event) => setInput(event.target.value)}
				/>
			</div>
			<fieldset className="space-y-3 rounded-md border p-4">
				<legend className="px-1 text-sm font-medium">
					Mock tool response
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
			<div>
				<Label htmlFor="case-fixture">Complete fixture (JSON)</Label>
				<Textarea
					id="case-fixture"
					rows={10}
					className="max-h-96 font-mono text-xs"
					value={fixture}
					onChange={(event) => setFixture(event.target.value)}
				/>
				<p className="mt-1 text-xs text-muted-foreground">
					Rules support tool, match_args, return and mutate. Entities
					map collection names to records by ID. Review all rules
					together before saving.
				</p>
			</div>
			<div>
				<Label htmlFor="case-assertions">Assertions (JSON)</Label>
				<Textarea
					id="case-assertions"
					rows={7}
					className="font-mono text-xs"
					value={assertions}
					onChange={(event) => setAssertions(event.target.value)}
				/>
				<p className="mt-1 text-xs text-muted-foreground">
					Check terminal_status, output_path, tool_called,
					tool_not_called, tool_count, tool_order, tool_args,
					simulator_state, max_tokens, max_cost_usd or max_latency_ms.
					Keep no_real_tools to verify isolation.
				</p>
			</div>
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
							? "Save draft changes"
							: "Freeze and add case"}
				</Button>
				<Button type="button" variant="outline" onClick={onCancel}>
					Cancel
				</Button>
			</div>
		</form>
	);
}
