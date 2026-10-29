import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useUsersFiltered } from "@/hooks/useUsers";
import { testAllActions, type FilePolicyAction } from "@/services/filePolicies";
import { InlineLoader } from "./InlineLoader";

interface TestAccessModalProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	location: string;
	scope: string | null;
	solution?: string;
	path: string;
	scopeLabel?: string;
}

export interface TestAccessPanelProps {
	location: string;
	scope: string | null;
	solution?: string;
	path: string;
	scopeLabel?: string;
	onOpenChange?: (open: boolean) => void;
}

const ACTIONS: FilePolicyAction[] = ["read", "write", "delete", "list"];

function formatScope(scope: string | null) {
	return !scope || scope === "global" ? "Global" : scope;
}

function formatPath(path: string) {
	return path || "Share root";
}

function readableRuleName(name: string) {
	if (name === "admin_bypass") return "Platform Admin Access";
	return name
		.split(/[_-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");
}

function formatAllowedReason(result: {
	matchedRule?: string | null;
	matchedPolicy?: string | null;
}) {
	if (result.matchedRule === "allowing rule")
		return "Allowed by the matching policy rule.";
	if (result.matchedRule) {
		return `Allowed by ${readableRuleName(result.matchedRule)}.`;
	}
	if (result.matchedPolicy) {
		return `Allowed by policy ${result.matchedPolicy}.`;
	}
	return "Access granted by the applicable policy.";
}

function TestAccessSession({
	location,
	scope,
	solution,
	path,
	scopeLabel,
	onOpenChange,
}: TestAccessPanelProps) {
	// This admin tool must include principals outside the share's scope. The
	// backend resolves the selected user's actual organization and roles.
	const usersQuery = useUsersFiltered(undefined);
	const [userId, setUserId] = useState("");
	const options = useMemo(
		() =>
			(usersQuery.data ?? []).map((user) => ({
				value: user.id,
				label: user.name ? `${user.name} (${user.email})` : user.email,
			})),
		[usersQuery.data],
	);
	const access = useQuery({
		queryKey: ["file-access-test", location, scope, solution, path, userId],
		queryFn: () =>
			testAllActions(
				solution
					? { location, scope, solution, path, userId }
					: { location, scope, path, userId },
			),
		enabled: !!userId,
		retry: false,
		gcTime: 0,
	});
	const results =
		userId && !access.isFetching && !access.isError
			? access.data
			: undefined;

	return (
		<section className="flex min-h-0 flex-1 flex-col overflow-hidden">
			<div className="shrink-0 space-y-2 border-b border-border/70 p-4">
				{onOpenChange && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className="-ml-2 min-h-11 justify-start px-2 text-xs text-muted-foreground"
						onClick={() => onOpenChange(false)}
					>
						<ArrowLeft className="size-4" />
						Back to Access
					</Button>
				)}
				<div className="flex min-h-6 items-center gap-2">
					<FlaskConical className="size-4 shrink-0 text-primary" />
					<h2 className="text-sm font-semibold">Test Access</h2>
				</div>
				<p className="text-sm text-muted-foreground">
					Choose a user to see the policy decision for each file
					action.
				</p>
				<p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
					<span className="font-medium text-foreground">
						{location}
					</span>
					{" / "}
					<span className="font-mono">{formatPath(path)}</span>
					{" / "}
					{scopeLabel ?? formatScope(scope)}
				</p>
			</div>
			<div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
				<div className="space-y-2">
					<Label htmlFor="test-access-user">User</Label>
					<Combobox
						id="test-access-user"
						className="min-h-11"
						options={options}
						value={userId}
						onValueChange={setUserId}
						placeholder="Select a user…"
						searchPlaceholder="Search users…"
						emptyText="No matching users."
						isLoading={usersQuery.isLoading}
						disabled={!options.length}
					/>
					{usersQuery.isError && (
						<div role="alert" className="space-y-2 text-sm">
							<p className="text-destructive">
								Couldn’t load users.
								{options.length
									? " Showing the last loaded users."
									: ""}
							</p>
							<Button
								variant="outline"
								className="min-h-11"
								disabled={usersQuery.isFetching}
								onClick={() => void usersQuery.refetch()}
							>
								Retry Users
							</Button>
						</div>
					)}
					{!usersQuery.isLoading &&
						!usersQuery.isError &&
						options.length === 0 && (
							<p className="text-sm text-muted-foreground">
								No users are available to test.
							</p>
						)}
				</div>
				{!userId && options.length > 0 && (
					<p className="text-sm text-muted-foreground">
						Select a user to check read, write, delete, and list
						access.
					</p>
				)}
				{!!userId && access.isFetching && (
					<InlineLoader label="Resolving access…" />
				)}
				{!!userId && access.isError && (
					<div role="alert" className="space-y-2 text-sm">
						<p className="text-destructive">
							Couldn’t test access. No access decision was
							returned.
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={access.isFetching}
							onClick={() => void access.refetch()}
						>
							Retry Test
						</Button>
					</div>
				)}
				{results && (
					<ul aria-label="Access test results" className="space-y-3">
						{ACTIONS.map((action) => {
							const result = results[action];
							return (
								<li
									key={action}
									className="space-y-2 rounded-[var(--bf-radius-surface)] border p-3 text-sm"
								>
									<div className="flex items-center justify-between gap-3">
										<span className="font-medium capitalize">
											{action}
										</span>
										<Badge
											className={
												result.allowed
													? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
													: undefined
											}
											variant={
												result.allowed
													? "secondary"
													: "destructive"
											}
										>
											{result.allowed
												? "Allowed"
												: "Denied"}
										</Badge>
									</div>
									<p className="text-muted-foreground [overflow-wrap:anywhere]">
										{result.allowed
											? formatAllowedReason(result)
											: (result.denialReason ??
												"No matching rule.")}
									</p>
								</li>
							);
						})}
					</ul>
				)}
			</div>
		</section>
	);
}

export function TestAccessPanel(props: TestAccessPanelProps) {
	return (
		<TestAccessSession
			key={JSON.stringify([
				props.location,
				props.scope,
				props.solution,
				props.path,
			])}
			{...props}
		/>
	);
}

export function TestAccessModal(props: TestAccessModalProps) {
	if (!props.open) return null;
	return (
		<Dialog open={props.open} onOpenChange={props.onOpenChange}>
			<DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-lg [&_[data-slot=dialog-header]]:pr-16">
				<DialogHeader className="sr-only">
					<DialogTitle>Test Access</DialogTitle>
					<DialogDescription>
						Check what a user can do with this file or folder.
					</DialogDescription>
				</DialogHeader>
				<TestAccessPanel
					location={props.location}
					scope={props.scope}
					solution={props.solution}
					path={props.path}
					scopeLabel={props.scopeLabel}
					onOpenChange={props.onOpenChange}
				/>
			</DialogContent>
		</Dialog>
	);
}
