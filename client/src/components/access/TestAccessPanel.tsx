import { useRef, useState } from "react";
import { AlertCircle, Building2, Globe, Loader2, Workflow } from "lucide-react";

import { AccessTraceStrip } from "@/components/access/AccessTraceStrip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import {
	Command,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { Label } from "@/components/ui/label";
import { useTraceNames } from "@/hooks/useTraceNames";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { OPERATION_SUGGESTIONS } from "@/lib/access-operations";
import { getErrorMessage } from "@/lib/api-error";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { useCheckUserAccess, type AccessTrace } from "@/services/access";
import { useAuthorization } from "@/services/authorization";

/** The organization choice the access check names Global by. */
const GLOBAL_CHOICE = "global";
const NO_WORKFLOW = "none";
/** What the caller needs where they test (the access check's own gate). */
const TEST_PERMISSION = "roleassignments.read";

function OperationInput({
	value,
	onChange,
}: {
	value: string;
	onChange: (operation: string) => void;
}) {
	const [suggesting, setSuggesting] = useState(false);
	return (
		// cmdk labels its input itself, so the field's name comes from `label`.
		<Command
			label="Operation"
			className="relative overflow-visible bg-transparent p-0 [&_[data-slot=command-input-wrapper]]:p-0"
		>
			<CommandInput
				aria-describedby="test-access-operation-help"
				placeholder="Read Tables, agents.list or GET /api/users"
				value={value}
				onValueChange={(next) => {
					onChange(next);
					setSuggesting(true);
				}}
				onFocus={() => setSuggesting(true)}
				onBlur={() => setSuggesting(false)}
			/>
			{suggesting && (
				<CommandList
					aria-label="Common Operations"
					className="absolute inset-x-0 top-full z-10 mt-1 max-h-60 rounded-[var(--bf-radius-control)] border bg-popover shadow-lg [&:not(:has([cmdk-item]))]:hidden"
					// Keep focus in the input so choosing doesn't blur the list away first.
					onMouseDown={(event) => event.preventDefault()}
				>
					<CommandGroup>
						{OPERATION_SUGGESTIONS.map((suggestion) => (
							<CommandItem
								key={suggestion.operation}
								value={suggestion.operation}
								keywords={[suggestion.label]}
								className="min-h-11 flex-col items-start gap-0"
								onSelect={() => {
									onChange(suggestion.operation);
									setSuggesting(false);
								}}
							>
								<span className="font-medium">
									{suggestion.label}
								</span>
								<span className="font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">
									{suggestion.operation}
								</span>
							</CommandItem>
						))}
					</CommandGroup>
				</CommandList>
			)}
		</Command>
	);
}

/**
 * Test what the managed-identity model would decide for a user or identity
 * (`subjectId`): an operation in an organization or Global, directly or
 * through a workflow. Report-only; nothing is recorded or enforced.
 * `defaultOrganizationId` is an organization id or "global".
 */
export function TestAccessPanel({
	subjectId,
	defaultWorkflowId,
	defaultOrganizationId,
}: {
	subjectId: string;
	defaultWorkflowId?: string;
	defaultOrganizationId?: string;
}) {
	const authorization = useAuthorization();
	const { names, organizationNames } = useTraceNames(subjectId);
	const workflowsQuery = useWorkflowsMetadata({
		enabled: authorization.canAnywhere("workflows.read"),
	});
	const check = useCheckUserAccess();

	const [organization, setOrganization] = useState(
		defaultOrganizationId ?? "",
	);
	const [operation, setOperation] = useState("");
	const [workflow, setWorkflow] = useState(defaultWorkflowId ?? NO_WORKFLOW);
	const [result, setResult] = useState<{ trace: AccessTrace; run: number }>();
	const [error, setError] = useState<string | null>(null);
	// Numbers each check; changing a selection moves it on, so an answer to
	// an earlier request is never shown under the new selections.
	const requestRef = useRef(0);

	const canTestAt = (organizationId: string | null) =>
		authorization.canAt(
			TEST_PERMISSION,
			organizationId ? orgTarget(organizationId) : GLOBAL_TARGET,
		);
	const organizationChoices: ComboboxOption[] = [
		...(canTestAt(null)
			? [{ value: GLOBAL_CHOICE, label: "Global", icon: Globe }]
			: []),
		...[...organizationNames]
			.filter(([id]) => canTestAt(id))
			.sort(([, a], [, b]) => a.localeCompare(b))
			.map(([id, name]) => ({ value: id, label: name, icon: Building2 })),
	];
	const workflowChoices: ComboboxOption[] = [
		{
			value: NO_WORKFLOW,
			label: "No Workflow",
			description: "They act directly",
		},
		...workflowsQuery.data.workflows.map((item) => ({
			value: item.id,
			label: item.display_name || item.name,
			icon: Workflow,
		})),
	];

	const changed =
		<T,>(set: (value: T) => void) =>
		(value: T) => {
			set(value);
			requestRef.current += 1;
			setResult(undefined);
			setError(null);
		};
	const workflowId = workflow && workflow !== NO_WORKFLOW ? workflow : null;
	const ready = organization !== "" && operation.trim() !== "";

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!ready || check.isPending) return;
		setError(null);
		const request = ++requestRef.current;
		try {
			const trace = await check.mutateAsync({
				params: { path: { user_id: subjectId } },
				body: {
					organization_id: organization,
					operation: operation.trim(),
					workflow_id: workflowId,
				},
			});
			if (request === requestRef.current)
				setResult({ trace, run: request });
		} catch (cause) {
			if (request !== requestRef.current) return;
			setResult(undefined);
			setError(getErrorMessage(cause, "The access check could not run."));
		}
	};

	return (
		<div className="space-y-6">
			<form className="space-y-4" onSubmit={handleSubmit}>
				<div className="space-y-2">
					<Label htmlFor="test-access-organization">
						Organization
					</Label>
					<Combobox
						id="test-access-organization"
						aria-label="Organization"
						options={organizationChoices}
						value={organization}
						onValueChange={changed(setOrganization)}
						placeholder="Choose where they act"
						searchPlaceholder="Search organizations"
						emptyText="No organization found."
					/>
				</div>
				<div className="space-y-2">
					<Label asChild>
						<span aria-hidden="true">Operation</span>
					</Label>
					<OperationInput
						value={operation}
						onChange={changed(setOperation)}
					/>
					<p
						id="test-access-operation-help"
						className="text-xs text-muted-foreground"
					>
						Choose a common operation, or type any access-list
						operation: a catalog id or METHOD /api/path.
					</p>
				</div>
				<div className="space-y-2">
					<Label htmlFor="test-access-workflow">Workflow</Label>
					<Combobox
						id="test-access-workflow"
						aria-label="Workflow"
						options={workflowChoices}
						value={workflow}
						onValueChange={changed(setWorkflow)}
						placeholder="No Workflow"
						searchPlaceholder="Search workflows"
						emptyText="No workflow found."
					/>
				</div>
				<Button
					type="submit"
					className="min-h-11"
					disabled={!ready || check.isPending}
				>
					{check.isPending && (
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
					)}
					Test Access
				</Button>
			</form>
			{error && (
				<Alert variant="destructive">
					<AlertCircle aria-hidden="true" className="size-4" />
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{result && (
				<AccessTraceStrip
					key={result.run}
					trace={result.trace}
					names={names}
				/>
			)}
		</div>
	);
}
