/**
 * SolutionSetupChecklist
 *
 * Presentational component that lists a solution's config declarations and
 * lets the user set values for any that are missing. The parent page owns
 * data-fetching and the real config-set mutation; this component is kept
 * deliberately side-effect-free so it's unit-testable without a network.
 */

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Circle, ExternalLink, KeyRound, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SolutionSetupItem } from "@/services/solutions";

export interface SolutionSetupChecklistProps {
	items: SolutionSetupItem[];
	setupComplete: boolean;
	/** Called when the user submits a value for a config key. */
	onSet: (key: string, value: string) => void | Promise<void>;
	onGenerateWorkflowKey?: (workflowId: string) => void | Promise<void>;
	/**
	 * Supplies the href for a connection item's "Set up integration" link.
	 * Defaults to the global Integrations page (opened in a new tab so the
	 * admin keeps the setup context). The integration name is passed in case a
	 * caller wants to deep-link.
	 */
	integrationHref?: (name: string) => string;
}

/** Default link target for connection items: the global Integrations page. */
export function defaultIntegrationHref(): string {
	return "/integrations";
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

export function ConfigItem({
	item,
	onSet,
}: {
	item: SolutionSetupItem;
	onSet: (key: string, value: string) => void | Promise<void>;
}) {
	const [value, setValue] = useState("");
	const [pending, setPending] = useState(false);
	const secret = isSecretType(item.type);
	const requiredUnset = item.required && !item.is_set;

	const handleSet = async () => {
		if (!value.trim()) return;
		setPending(true);
		try {
			await onSet(item.key, value);
			setValue("");
		} finally {
			setPending(false);
		}
	};

	const placeholder = item.is_set
		? "Enter a new value…"
		: item.default
			? `Default: ${item.default}`
			: "Enter a value…";

	return (
		<div
			className={
				"rounded-lg border p-4 " +
				(requiredUnset ? "border-yellow-500/60 bg-yellow-500/5" : "")
			}
		>
			{/* Key + meta row */}
			<div className="flex items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<span className="truncate font-mono text-sm font-medium">
						{item.key}
					</span>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						{item.type}
					</Badge>
					{item.required && (
						<span className="shrink-0 text-xs text-destructive">required</span>
					)}
				</div>
				<span
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(item.is_set
							? "text-green-600 dark:text-green-500"
							: "text-muted-foreground")
					}
				>
					{item.is_set ? (
						<CheckCircle2 className="h-3.5 w-3.5" />
					) : (
						<Circle className="h-3.5 w-3.5" />
					)}
					{item.is_set ? "Set" : "Not set"}
				</span>
			</div>

			{item.description && (
				<p className="mt-1 text-xs text-muted-foreground">{item.description}</p>
			)}

			{/* Value input — always rendered so the user can override an existing value */}
			<div className="mt-3 flex items-center gap-2">
				<Input
					data-testid={`config-value-input-${item.key}`}
					type={secret ? "password" : "text"}
					value={value}
					placeholder={placeholder}
					onChange={(e) => setValue(e.target.value)}
					onKeyDown={(e) => {
						if (e.key === "Enter" && value.trim()) {
							void handleSet();
						}
					}}
				/>
				{(value.trim() || requiredUnset) && (
					<Button
						aria-label={`Set ${item.key}`}
						disabled={!value.trim() || pending}
						onClick={() => void handleSet()}
					>
						{pending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
						Set
					</Button>
				)}
			</div>
		</div>
	);
}

export function ConnectionItem({
	item,
	integrationHref = defaultIntegrationHref,
}: {
	item: SolutionSetupItem;
	integrationHref?: (name: string) => string;
}) {
	// `is_set` for a connection means the global Integration shell exists. A
	// missing shell is the blocking case; OAuth is a warn-only nudge handled
	// separately below.
	const requiredUnset = item.required && !item.is_set;
	// Warn-only: the connection's template carried an OAuth shape but no token
	// resolves yet. This must NOT gate the wizard — it's a reminder, not a block.
	const showOauthWarning = item.has_oauth && !item.connected;

	return (
		<div
			className={
				"rounded-lg border p-4 " +
				(requiredUnset ? "border-amber-500/60 bg-amber-500/5" : "")
			}
		>
			{/* Name + meta row */}
			<div className="flex items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<span className="truncate text-sm font-medium">{item.key}</span>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						integration
					</Badge>
					{item.required && (
						<span className="shrink-0 text-xs text-destructive">required</span>
					)}
				</div>
				<span
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(item.connected
							? "text-green-600 dark:text-green-500"
							: "text-muted-foreground")
					}
				>
					{item.connected ? (
						<CheckCircle2 className="h-3.5 w-3.5" />
					) : (
						<Circle className="h-3.5 w-3.5" />
					)}
					{item.connected ? "Connected" : item.is_set ? "Not connected" : "Missing"}
				</span>
			</div>

			{item.description && (
				<p className="mt-1 text-xs text-muted-foreground">{item.description}</p>
			)}

			{showOauthWarning && (
				<div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
					<AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
					<span>
						This integration uses OAuth — connect it (client ID/secret +
						authorize) in Integrations. You can still finish setup; this is a
						reminder, not a blocker.
					</span>
				</div>
			)}

			{/* External link to the global Integrations page (new tab). */}
			<div className="mt-3">
				<Button asChild variant="outline" size="sm">
					<a
						href={integrationHref(item.key)}
						target="_blank"
						rel="noopener noreferrer"
					>
						<ExternalLink className="mr-1.5 h-3.5 w-3.5" />
						Set up integration
					</a>
				</Button>
			</div>
		</div>
	);
}

export function WorkflowEndpointKeyItem({
	item,
	onGenerateWorkflowKey,
}: {
	item: SolutionSetupItem;
	onGenerateWorkflowKey?: (workflowId: string) => void | Promise<void>;
}) {
	const [pending, setPending] = useState(false);
	const workflowId = item.workflow_id ?? item.key;
	const label = item.workflow_name ?? item.key;
	const methods = item.allowed_methods?.length
		? item.allowed_methods.join(", ")
		: "POST";

	const handleGenerate = async () => {
		if (!workflowId || !onGenerateWorkflowKey) return;
		setPending(true);
		try {
			await onGenerateWorkflowKey(workflowId);
		} finally {
			setPending(false);
		}
	};

	return (
		<div
			className={
				"rounded-lg border p-4 " +
				(item.required && !item.is_set ? "border-yellow-500/60 bg-yellow-500/5" : "")
			}
		>
			<div className="flex items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
					<span className="truncate text-sm font-medium">{label}</span>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						endpoint key
					</Badge>
					{item.required && (
						<span className="shrink-0 text-xs text-destructive">required</span>
					)}
				</div>
				<span
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(item.is_set
							? "text-green-600 dark:text-green-500"
							: "text-muted-foreground")
					}
				>
					{item.is_set ? (
						<CheckCircle2 className="h-3.5 w-3.5" />
					) : (
						<Circle className="h-3.5 w-3.5" />
					)}
					{item.is_set ? "Key generated" : "Missing"}
				</span>
			</div>
			<p className="mt-1 text-xs text-muted-foreground">
				{methods} endpoint callers need an API key.
			</p>
			<div className="mt-3">
				<Button
					variant={item.is_set ? "outline" : "default"}
					size="sm"
					disabled={pending || !onGenerateWorkflowKey}
					onClick={() => void handleGenerate()}
				>
					{pending ? (
						<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
					) : (
						<KeyRound className="mr-1.5 h-3.5 w-3.5" />
					)}
					{item.is_set ? "Rotate endpoint key" : "Generate endpoint key"}
				</Button>
			</div>
		</div>
	);
}

export function SolutionSetupChecklist({
	items,
	setupComplete,
	onSet,
	onGenerateWorkflowKey,
	integrationHref,
}: SolutionSetupChecklistProps) {
	if (items.length === 0) {
		return (
			<div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">
				This Solution declares no configuration.
			</div>
		);
	}

	return (
		<div className="space-y-3">
			{setupComplete && (
				<div
					data-testid="setup-complete-banner"
					className="flex items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/5 px-4 py-3 text-sm text-green-700 dark:text-green-400"
				>
					<CheckCircle2 className="h-4 w-4 shrink-0" />
					All required configs are set — this Solution is ready to run.
				</div>
			)}
			{items.map((item) =>
				item.kind === "connection" ? (
						<ConnectionItem
							key={`connection:${item.key}`}
							item={item}
							integrationHref={integrationHref}
						/>
					) : item.kind === "workflow_endpoint_key" ? (
						<WorkflowEndpointKeyItem
							key={`workflow-endpoint-key:${item.key}`}
							item={item}
							onGenerateWorkflowKey={onGenerateWorkflowKey}
						/>
					) : (
						<ConfigItem key={`config:${item.key}`} item={item} onSet={onSet} />
					),
			)}
		</div>
	);
}
