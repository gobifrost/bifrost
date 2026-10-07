import { useState, type ReactNode } from "react";
import { AlertCircle, Archive, FlaskConical, Loader2 } from "lucide-react";

import { AccessTraceStrip } from "@/components/access/AccessTraceStrip";
import { RunUserLabel } from "@/components/audit/RunUserLabel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { useAuditExplain, type AuditLogEntry } from "@/hooks/useAuditLog";
import { isNotFoundError } from "@/hooks/useExecutions";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useTraceNames } from "@/hooks/useTraceNames";
import { useWorkflowsMetadata } from "@/hooks/useWorkflows";
import {
	changeSentence,
	changedStepKeys,
	checkKindTitle,
	nowUnavailableSentence,
	stepTitle,
	storedTrace,
	type TraceNames,
} from "@/lib/access-trace";
import { getErrorMessage } from "@/lib/api-error";
import { cn, formatRelativeTime } from "@/lib/utils";
import { AuditOutcome } from "@/pages/audit/AuditOutcome";
import { useAuthorization } from "@/services/authorization";

const ACCESS_CHECK = "access.check";

function Field({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="grid gap-1 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-3">
			<dt className="text-xs font-medium text-muted-foreground sm:pt-0.5">
				{label}
			</dt>
			<dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
		</div>
	);
}

function detailValue(value: unknown): string {
	return typeof value === "string" ? value : JSON.stringify(value);
}

function Summary({
	entry,
	names,
}: {
	entry: AuditLogEntry;
	names: TraceNames;
}) {
	const authorization = useAuthorization();
	const isCheck = entry.action === ACCESS_CHECK;
	const workflowId = isCheck ? entry.details?.workflow_id : undefined;
	const workflows = useWorkflowsMetadata({
		enabled:
			typeof workflowId === "string" &&
			authorization.canAnywhere("workflows.read"),
	}).data.workflows;
	const workflow =
		typeof workflowId === "string"
			? workflows.find((item) => item.id === workflowId)
			: undefined;
	const resource = entry.resource_type
		? isCheck
			? checkKindTitle(entry.resource_type)
			: `${entry.resource_type}${entry.resource_id ? ` / ${entry.resource_id}` : ""}`
		: "—";

	return (
		<dl className="space-y-3 text-sm">
			<Field label="Action">
				<Badge variant="secondary" className="font-mono">
					{entry.action}
				</Badge>
			</Field>
			<Field label="Outcome">
				<AuditOutcome outcome={entry.outcome} />
			</Field>
			<Field label="Time">
				<time dateTime={entry.timestamp}>
					{new Date(entry.timestamp).toLocaleString()}
				</time>
				<span className="text-muted-foreground">
					{" "}
					· {formatRelativeTime(entry.timestamp)}
				</span>
			</Field>
			<Field label="Actor">
				<RunUserLabel
					entry={entry}
					organizationName={names.organization}
				/>
			</Field>
			<Field label="Organization">
				{entry.actor.organization_name ??
					(entry.actor.organization_id ? "—" : "Global")}
			</Field>
			<Field label="Resource">{resource}</Field>
			{typeof workflowId === "string" && (
				<Field label="Workflow">
					{workflow ? (
						workflow.display_name || workflow.name
					) : (
						<span className="font-mono text-xs">{workflowId}</span>
					)}
				</Field>
			)}
			{isCheck && entry.operation_id && (
				<Field label="Operation">
					<span className="font-mono text-xs">
						{entry.operation_id}
					</span>
				</Field>
			)}
		</dl>
	);
}

function Details({ details }: { details: Record<string, unknown> }) {
	const fields = Object.entries(details);
	if (fields.length === 0) return null;
	return (
		<section aria-labelledby="audit-event-details" className="space-y-3">
			<h3 id="audit-event-details" className="text-sm font-semibold">
				Details
			</h3>
			<dl className="space-y-3 text-sm">
				{fields.map(([key, value]) => (
					<Field
						key={key}
						label={stepTitle(key.replaceAll("_", " "))}
					>
						<span className="whitespace-pre-wrap font-mono text-xs">
							{detailValue(value)}
						</span>
					</Field>
				))}
			</dl>
		</section>
	);
}

function Column({ title, children }: { title: string; children: ReactNode }) {
	return (
		<section aria-label={title} className="min-w-0 space-y-3">
			<h3 className="text-sm font-semibold">{title}</h3>
			{children}
		</section>
	);
}

/** The stored trace, and on request the same check judged again now beside it. */
function AccessCheckTrace({
	entry,
	names,
}: {
	entry: AuditLogEntry;
	names: TraceNames;
}) {
	const [requested, setRequested] = useState(false);
	const explain = useAuditExplain(entry.id, requested);
	const then = storedTrace(entry.details);
	const explanation = explain.data;
	const changed =
		explanation?.then &&
		explanation.now &&
		changedStepKeys(explanation.then, explanation.now);
	const compared = Boolean(explanation && !explain.error);

	const testAgain = () => {
		if (requested) void explain.refetch();
		else setRequested(true);
	};

	return (
		<section aria-labelledby="audit-event-trace" className="space-y-4">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<h3 id="audit-event-trace" className="text-sm font-semibold">
					Access Trace
				</h3>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 sm:min-h-9"
					disabled={explain.isFetching}
					onClick={testAgain}
				>
					{explain.isFetching ? (
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
					) : (
						<FlaskConical aria-hidden="true" className="size-4" />
					)}
					Test Again Now
				</Button>
			</div>
			{explain.error ? (
				isNotFoundError(explain.error) ? (
					<Alert>
						<Archive aria-hidden="true" className="size-4" />
						<AlertDescription>
							This event is archived. Export it to see its
							details.
						</AlertDescription>
					</Alert>
				) : (
					<Alert variant="destructive">
						<AlertCircle aria-hidden="true" className="size-4" />
						<AlertDescription>
							{getErrorMessage(
								explain.error,
								"The check could not be tested again.",
							)}
						</AlertDescription>
					</Alert>
				)
			) : null}
			{compared && explanation && (
				<p role="status" className="text-sm font-medium">
					{explanation.now
						? changeSentence(explanation.then, explanation.now)
						: explanation.now_unavailable &&
							nowUnavailableSentence(explanation.now_unavailable)}
				</p>
			)}
			<div
				className={cn(
					"grid gap-6",
					compared && explanation?.now && "lg:grid-cols-2",
				)}
			>
				{then && (
					<Column title="Then">
						<AccessTraceStrip
							trace={then}
							names={names}
							label="Then"
						/>
					</Column>
				)}
				{compared && explanation?.now && (
					<Column title="Now">
						<AccessTraceStrip
							trace={explanation.now}
							names={names}
							label="Now"
							changed={changed || undefined}
						/>
					</Column>
				)}
			</div>
		</section>
	);
}

/**
 * One audit event in a side sheet (a bottom sheet on phones): what happened,
 * who did it, where and to what. An access check also shows its stored
 * trace and can be tested again now; any other event lists its details.
 * Closing returns focus to `returnFocus` (the row that opened it).
 */
export function AuditEventDrawer({
	entry,
	open,
	onOpenChange,
	returnFocus,
}: {
	entry: AuditLogEntry | undefined;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	returnFocus?: HTMLElement | null;
}) {
	const wide = useMediaQuery("(min-width: 640px)");
	const isCheck = entry?.action === ACCESS_CHECK;
	const { names } = useTraceNames(
		isCheck ? (entry?.actor.user_id ?? undefined) : undefined,
	);

	return (
		<Sheet open={open && entry !== undefined} onOpenChange={onOpenChange}>
			<SheetContent
				side={wide ? "right" : "bottom"}
				className={cn(
					wide
						? isCheck
							? "sm:max-w-2xl lg:max-w-4xl"
							: "sm:max-w-xl"
						: "max-h-[90dvh]",
				)}
				onCloseAutoFocus={(event) => {
					if (!returnFocus) return;
					event.preventDefault();
					returnFocus.focus();
				}}
			>
				{entry && (
					<>
						<SheetHeader className="border-b border-border/70">
							<SheetTitle>
								{isCheck ? "Access Check" : "Audit Event"}
							</SheetTitle>
							<SheetDescription>
								{isCheck
									? "A report-only check of what the access model decides. Nothing was blocked."
									: "What happened, who did it, and where."}
							</SheetDescription>
						</SheetHeader>
						<div className="min-h-0 space-y-6 overflow-auto p-6">
							<Summary entry={entry} names={names} />
							{isCheck ? (
								<AccessCheckTrace
									key={entry.id}
									entry={entry}
									names={names}
								/>
							) : (
								entry.details && (
									<Details details={entry.details} />
								)
							)}
						</div>
					</>
				)}
			</SheetContent>
		</Sheet>
	);
}
