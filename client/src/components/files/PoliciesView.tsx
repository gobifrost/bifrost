import { useQuery } from "@tanstack/react-query";
import { Search, ShieldCheck, Trash2, FolderKey, Link2 } from "lucide-react";
import { useMemo, useState } from "react";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	listFilePolicies,
	type FilePolicy,
	type PolicyRuleRef,
} from "@/services/filePolicies";
import { InlineLoader } from "./InlineLoader";

type PolicyRule = FilePolicy["policies"]["policies"][number];

const formatPath = (path: string) =>
	path ? (path.startsWith("/") ? path : `/${path}`) : "/";
const formatIdentity = (policy: FilePolicy) =>
	`${policy.location}${formatPath(policy.path)}`;
const isPolicyRuleRef = (rule: PolicyRule): rule is PolicyRuleRef =>
	"$ref" in rule;
const readableRuleName = (name: string) =>
	name === "admin_bypass"
		? "Platform Admin Access"
		: name.replace(/[_-]+/g, " ");
const describeRule = (rule: PolicyRule) =>
	isPolicyRuleRef(rule)
		? `Shared rule: ${readableRuleName(rule.$ref)}`
		: `${rule.name}${rule.actions.length ? ` - ${rule.actions.join(", ")}` : ""}`;

interface PoliciesViewProps {
	scope: string | null;
	solution?: string;
	readOnly?: boolean;
	location?: string | null;
	prefix?: string;
	/** Bump to force a refetch after a policy mutation elsewhere. */
	refreshKey: number;
	onEdit: (policy: FilePolicy) => void;
	onDelete: (policy: FilePolicy) => void;
}

/**
 * Flat list of every file policy in the current scope — the "manage all
 * policies" surface the per-item "Manage policy" modal can't provide. Lists
 * across all locations; each row opens the policy editor for that exact
 * (location, path).
 */
export function PoliciesView({
	scope,
	solution,
	readOnly = false,
	location = null,
	prefix = "",
	refreshKey,
	onEdit,
	onDelete,
}: PoliciesViewProps) {
	const [query, setQuery] = useState("");
	const policiesQuery = useQuery({
		queryKey: ["file-policies", scope, solution, refreshKey],
		queryFn: () =>
			solution
				? listFilePolicies({ scope: scope ?? undefined, solution })
				: listFilePolicies({ scope: scope ?? undefined }),
		retry: false,
	});
	const policies = useMemo(
		() => policiesQuery.data?.policies ?? [],
		[policiesQuery.data],
	);
	const hasData = policiesQuery.data !== undefined;
	const showInitialLoading = policiesQuery.isPending && !hasData;
	const showEmptyState =
		!showInitialLoading && !policiesQuery.isError && policies.length === 0;
	const showError = policiesQuery.isError && !hasData;
	const showTransientError = policiesQuery.isError && hasData;

	const directoryPolicies = useMemo(
		() =>
			policies.filter((policy) => {
				if (location && policy.location !== location) return false;
				const directory = prefix.replace(/\/$/, "");
				return (
					!directory ||
					policy.path.replace(/\/$/, "") === directory ||
					policy.path.startsWith(`${directory}/`)
				);
			}),
		[policies, location, prefix],
	);
	const filteredPolicies = useMemo(() => {
		const normalizedQuery = query.trim().toLowerCase();
		if (!normalizedQuery) return directoryPolicies;
		return directoryPolicies.filter((policy) => {
			const haystack = [
				policy.location,
				formatPath(policy.path),
				formatIdentity(policy),
				...policy.policies.policies.map(describeRule),
			]
				.join(" ")
				.toLowerCase();
			return haystack.includes(normalizedQuery);
		});
	}, [directoryPolicies, query]);
	const renderRule = (rule: PolicyRule, index: number) => {
		const key = isPolicyRuleRef(rule) ? rule.$ref : `${rule.name}:${index}`;
		return (
			<Badge
				key={key}
				variant="outline"
				className="h-auto max-w-full items-start whitespace-normal [overflow-wrap:anywhere] rounded-[var(--bf-radius-control)] px-2 py-1 text-left leading-5"
			>
				{"$ref" in rule && (
					<Link2
						aria-hidden="true"
						className="mt-0.5 size-3 shrink-0 text-primary"
					/>
				)}
				{describeRule(rule)}
			</Badge>
		);
	};

	const renderActions = (policy: FilePolicy) =>
		!readOnly && (
			<RecordActionsMenu
				label={`Policy ${formatIdentity(policy)} actions`}
			>
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onEdit(policy)}
				>
					<ShieldCheck aria-hidden="true" className="size-4" />
					Manage Policy
				</DropdownMenuItem>
				<DropdownMenuItem
					variant="destructive"
					className="min-h-11"
					onSelect={() => onDelete(policy)}
				>
					<Trash2 aria-hidden="true" className="size-4" />
					Delete
				</DropdownMenuItem>
			</RecordActionsMenu>
		);

	const renderPolicy = (policy: FilePolicy) => (
		<li key={policy.id ?? `${policy.location}:${policy.path}`}>
			<div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 border-b px-4 py-3 hover:bg-muted/20">
				<button
					type="button"
					className="min-h-11 min-w-0 rounded-[var(--bf-radius-control)] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					disabled={readOnly}
					onClick={readOnly ? undefined : () => onEdit(policy)}
					aria-label={`${readOnly ? "Policy" : "Manage policy"} for ${formatIdentity(policy)}`}
				>
					<span className="flex items-center gap-2 text-sm font-semibold leading-6 [overflow-wrap:anywhere]">
						<FolderKey
							aria-hidden="true"
							className="size-4 shrink-0 text-primary"
						/>
						{policy.path
							? policy.path.replace(/\/$/, "").split("/").at(-1)
							: policy.location}
					</span>
					<span className="block text-xs text-muted-foreground [overflow-wrap:anywhere]">
						{policy.path
							? `${policy.location}${formatPath(policy.path)}`
							: "Share-wide policy"}
					</span>
				</button>
				<div className="col-start-1 flex min-w-0 flex-wrap gap-1.5">
					{policy.policies.policies.length === 0 ? (
						<span className="text-xs text-muted-foreground">
							No rules
						</span>
					) : (
						policy.policies.policies.map(renderRule)
					)}
				</div>
				{renderActions(policy) && (
					<div className="col-start-2 row-start-1 shrink-0 justify-self-end">
						{renderActions(policy)}
					</div>
				)}
			</div>
		</li>
	);

	const renderDirectory = () => (
		<div className="flex h-full min-h-0 flex-col">
			<div className="shrink-0 border-b px-4 py-3">
				<p className="mb-3 text-xs leading-5 text-muted-foreground">
					{location
						? "Policies attached to this folder and its descendants. Inherited access is shown in Folder Details."
						: "Policies define access to a share, folder, or file. Select a folder to narrow this list."}
				</p>
				<div className="flex flex-wrap items-center justify-between gap-3">
					<div className="min-w-0">
						<h2 className="text-sm font-semibold leading-5">
							Access Policies
						</h2>
						<p className="text-xs text-muted-foreground">
							{filteredPolicies.length} of{" "}
							{directoryPolicies.length}{" "}
							{directoryPolicies.length === 1
								? "policy"
								: "policies"}
						</p>
					</div>
					<label className="relative min-w-0 flex-1 sm:max-w-xs">
						<span className="sr-only">Search policies</span>
						<Search
							aria-hidden="true"
							className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
						/>
						<Input
							value={query}
							onChange={(event) => setQuery(event.target.value)}
							placeholder="Search policies"
							className="pl-9"
						/>
					</label>
				</div>
			</div>
			{filteredPolicies.length === 0 ? (
				<p className="p-4 text-sm text-muted-foreground">
					{query
						? "No policies match this search."
						: "No policies are set here. Open Folder Details to see inherited access."}
				</p>
			) : (
				<ul className="min-h-0 flex-1 overflow-auto">
					{filteredPolicies.map(renderPolicy)}
				</ul>
			)}
		</div>
	);

	return (
		<section aria-label="Access Policies" className="h-full min-h-0">
			{showInitialLoading ? (
				<InlineLoader className="p-4" />
			) : showError ? (
				<Alert
					variant="destructive"
					className="mx-4 my-4 w-[calc(100%-2rem)] rounded-[var(--bf-radius-surface)]"
				>
					<AlertTitle>File policies could not be loaded</AlertTitle>
					<AlertDescription>
						<p>Try again to load the policies for this scope.</p>
						<Button
							type="button"
							variant="outline"
							className="mt-3 min-h-11"
							onClick={() => void policiesQuery.refetch()}
						>
							Retry policies
						</Button>
					</AlertDescription>
				</Alert>
			) : showEmptyState ? (
				<p className="p-4 text-sm text-muted-foreground">
					No policies in this scope yet.
				</p>
			) : (
				<>
					{showTransientError && (
						<Alert
							variant="destructive"
							className="mx-4 my-4 w-[calc(100%-2rem)] rounded-[var(--bf-radius-surface)]"
						>
							<AlertTitle>
								File policies could not be refreshed
							</AlertTitle>
							<AlertDescription>
								<p>
									The current list is still shown, but the
									latest scope refresh failed.
								</p>
								<Button
									type="button"
									variant="outline"
									className="mt-3 min-h-11"
									onClick={() => void policiesQuery.refetch()}
								>
									Retry policies
								</Button>
							</AlertDescription>
						</Alert>
					)}
					{renderDirectory()}
				</>
			)}
		</section>
	);
}
