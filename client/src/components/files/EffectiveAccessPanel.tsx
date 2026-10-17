import { useQuery } from "@tanstack/react-query";
import {
	FileText,
	FlaskConical,
	FolderTree,
	KeyRound,
	ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import {
	effectiveAccess,
	type FilePolicy,
	type FilePolicyRule,
	type PolicyRuleRef,
} from "@/services/filePolicies";
import { listPolicyRules } from "@/services/policyRules";
import { InlineLoader } from "./InlineLoader";

interface EffectiveAccessPanelProps {
	location: string;
	scope: string | null;
	solution?: string;
	path: string | null;
	readOnly?: boolean;
	managedBySolution?: boolean;
	solutionId?: string | null;
	onOpenTest: () => void;
	onManagePolicy: () => void;
	onOpenPolicy?: (policy: FilePolicy) => void;
}

export function EffectiveAccessPanel({
	location,
	scope,
	solution,
	path,
	readOnly = false,
	managedBySolution = false,
	solutionId = null,
	onOpenTest,
	onManagePolicy,
	onOpenPolicy,
}: EffectiveAccessPanelProps) {
	const accessQuery = useQuery({
		queryKey: ["effective-access", location, scope, solution, path],
		queryFn: () =>
			solution
				? effectiveAccess(location, path ?? "", scope, solution)
				: effectiveAccess(location, path ?? "", scope),
		enabled: path !== null,
		retry: false,
	});
	const policies = accessQuery.data ?? [];
	const referencedRuleNames = Array.from(
		new Set(
			policies.flatMap((policy) =>
				policy.policies.policies.flatMap((rule) =>
					isPolicyRuleRef(rule) ? [rule.$ref] : [],
				),
			),
		),
	);
	const rulesQuery = useQuery({
		queryKey: ["policy-rules", "file"],
		queryFn: () => listPolicyRules("file"),
		enabled: referencedRuleNames.length > 0,
		retry: false,
	});
	const namedRules = new Map(
		(rulesQuery.data ?? []).map((rule) => [rule.name, rule]),
	);
	const error = accessQuery.isError;
	const loading = accessQuery.isLoading || accessQuery.isFetching;
	const formatPath = (policyPath: string) =>
		policyPath
			? policyPath.startsWith("/")
				? policyPath
				: `/${policyPath}`
			: "/";
	const sourceLabel = (policyPath: string) => {
		if (!policyPath) return "From share root";
		return policyPath === path
			? "Set on this file/folder"
			: `Inherited from ${formatPath(policyPath)}`;
	};
	const governingPolicy = policies[0] ?? null;
	const fallbackPolicies = policies.slice(1);

	return (
		<section
			aria-label="Effective access"
			className="flex h-full min-h-0 min-w-0 flex-col"
		>
			<div className="flex shrink-0 flex-col gap-3 border-b px-3 py-3">
				<div className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-semibold">
					<ShieldCheck className="size-4 text-primary" />
					<h2 className="min-w-0 leading-5">Effective Access</h2>
					{managedBySolution && (
						<SolutionManagedBadge
							solutionId={solutionId ?? undefined}
						/>
					)}
				</div>
				<div className="flex flex-wrap gap-2">
					{!readOnly && (
						<Button
							type="button"
							variant="outline"
							size="default"
							className="min-h-11"
							disabled={path === null}
							onClick={onManagePolicy}
						>
							<ShieldCheck className="size-4" />
							Manage Policy
						</Button>
					)}
					<Button
						type="button"
						size="default"
						className="min-h-11"
						onClick={onOpenTest}
						disabled={path === null}
					>
						<FlaskConical className="size-4" />
						Test Access
					</Button>
				</div>
			</div>
			<div className="min-h-0 min-w-0 flex-1 overflow-auto p-3 text-sm">
				{path === null && (
					<p className="[overflow-wrap:anywhere] text-muted-foreground">
						Select an item to see what governs it.
					</p>
				)}
				{loading && <InlineLoader label="Resolving…" />}
				{error && (
					<div role="alert" className="space-y-2">
						<p className="[overflow-wrap:anywhere] text-destructive">
							Couldn’t resolve access for this item.
						</p>
						<Button
							type="button"
							variant="outline"
							size="default"
							className="min-h-11"
							disabled={accessQuery.isFetching}
							onClick={() => void accessQuery.refetch()}
						>
							Retry access
						</Button>
					</div>
				)}
				{path !== null &&
					!loading &&
					!error &&
					policies.length === 0 && (
						<div className="space-y-2 rounded-md border border-dashed p-3">
							<div className="flex items-center gap-2 font-medium">
								<ShieldCheck className="size-4 text-muted-foreground" />
								No matching policy
							</div>
							<p className="[overflow-wrap:anywhere] text-muted-foreground">
								Access is denied by default until a policy rule
								grants a specific action.
							</p>
						</div>
					)}
				{governingPolicy && (
					<div className="mb-3 space-y-2 rounded-md border bg-muted/20 p-3">
						<div className="flex flex-wrap items-start justify-between gap-2">
							<div className="min-w-0 space-y-1">
								<div className="flex flex-wrap items-center gap-2">
									<Badge variant="secondary">
										{governingPolicy.path.replace(
											/\/$/,
											"",
										) !== (path ?? "").replace(/\/$/, "")
											? "Inherited Access"
											: "Policy on This Path"}
									</Badge>
									<Badge variant="outline">
										{sourceLabel(governingPolicy.path)}
									</Badge>
								</div>
								<p className="[overflow-wrap:anywhere] text-xs text-muted-foreground">
									Only this nearest matching policy is used
									for access checks. Use Test Access to
									evaluate a user and action.
								</p>
							</div>
							{onOpenPolicy && (
								<Button
									type="button"
									variant="ghost"
									size="sm"
									className="min-h-11"
									onClick={() =>
										onOpenPolicy(governingPolicy)
									}
								>
									<FileText className="size-4" />
									Open Source
								</Button>
							)}
						</div>
						<PolicySummary
							policy={governingPolicy}
							namedRules={namedRules}
							rulesLoading={rulesQuery.isFetching}
						/>
					</div>
				)}
				{fallbackPolicies.length > 0 && (
					<div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
						<FolderTree className="size-3.5" />
						Inherited fallback policies
					</div>
				)}
				<ul className="space-y-2">
					{fallbackPolicies.map((policy) => (
						<li
							key={
								policy.id ?? `${policy.location}:${policy.path}`
							}
							className="rounded-md border p-3"
						>
							<div className="flex flex-wrap items-center gap-2">
								<Badge variant="outline">
									{sourceLabel(policy.path)}
								</Badge>
								<span className="min-w-0 [overflow-wrap:anywhere] text-xs text-muted-foreground">
									{policy.location}
									{formatPath(policy.path)}
								</span>
							</div>
							<PolicySummary
								policy={policy}
								namedRules={namedRules}
								rulesLoading={rulesQuery.isFetching}
							/>
						</li>
					))}
				</ul>
			</div>
		</section>
	);
}

export function PolicySummary({
	policy,
	namedRules,
	rulesLoading,
}: {
	policy: FilePolicy;
	namedRules: Map<string, { description?: string | null; body?: unknown }>;
	rulesLoading: boolean;
}) {
	if (policy.policies.policies.length === 0) {
		return (
			<p className="mt-2 text-xs text-muted-foreground">
				This policy has no rules, so it denies every action.
			</p>
		);
	}
	return (
		<ul className="mt-2 space-y-2">
			{policy.policies.policies.map((rule, index) => {
				const summary = summarizeRule(rule, namedRules);
				return (
					<li key={summary.key ?? index} className="space-y-1">
						<div className="flex flex-wrap items-center gap-2">
							<KeyRound className="size-3.5 text-muted-foreground" />
							<span className="[overflow-wrap:anywhere] font-medium">
								{summary.name}
							</span>
							{summary.source && (
								<Badge variant="outline">
									{summary.source}
								</Badge>
							)}
						</div>
						{summary.description && (
							<p className="[overflow-wrap:anywhere] text-xs text-muted-foreground">
								{summary.description}
							</p>
						)}
						<div className="flex flex-wrap gap-1.5">
							{summary.actions.map((action) => (
								<Badge key={action} variant="secondary">
									{formatAction(action)}
								</Badge>
							))}
							{summary.actions.length === 0 && (
								<span className="text-xs text-muted-foreground">
									{rulesLoading
										? "Resolving named rule..."
										: "Rule actions unavailable"}
								</span>
							)}
						</div>
					</li>
				);
			})}
		</ul>
	);
}

function summarizeRule(
	rule: FilePolicyRule | PolicyRuleRef,
	namedRules: Map<string, { description?: string | null; body?: unknown }>,
) {
	if (isPolicyRuleRef(rule)) {
		const resolved = namedRules.get(rule.$ref);
		const body =
			typeof resolved?.body === "object" && resolved.body !== null
				? (resolved.body as { actions?: unknown })
				: null;
		const actions = Array.isArray(body?.actions)
			? body.actions.filter(
					(action): action is string => typeof action === "string",
				)
			: [];
		return {
			key: `ref:${rule.$ref}`,
			name: humanizeRuleName(rule.$ref),
			description:
				resolved?.description ??
				`Named policy rule "${rule.$ref}" is referenced here.`,
			actions,
			source: "Named rule",
		};
	}
	return {
		key: rule.name,
		name: humanizeRuleName(rule.name),
		description: rule.description ?? null,
		actions: rule.actions,
		source: "Inline rule",
	};
}

function isPolicyRuleRef(
	rule: FilePolicyRule | PolicyRuleRef,
): rule is PolicyRuleRef {
	return "$ref" in rule;
}

function humanizeRuleName(name: string) {
	if (name === "admin_bypass") return "Platform Admin Access";
	return name
		.split(/[_-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}

function formatAction(action: string) {
	return action.charAt(0).toUpperCase() + action.slice(1);
}
