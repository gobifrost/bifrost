import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Combobox } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useUsersFiltered } from "@/hooks/useUsers";
import {
	testAllActions,
	type FileAccessTestResult,
	type FilePolicyAction,
} from "@/services/filePolicies";
import { InlineLoader } from "./InlineLoader";

interface TestAccessModalProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	location: string;
	scope: string | null;
	path: string;
}

const ACTIONS: FilePolicyAction[] = ["read", "write", "delete", "list"];

export function TestAccessModal({
	open,
	onOpenChange,
	location,
	scope,
	path,
}: TestAccessModalProps) {
	// List ALL users (admin-only modal): an admin tests any principal against
	// this path regardless of the share's scope. The backend resolves the
	// picked user's real org/roles when evaluating access. Scoping the list to
	// the share's org would hide valid principals (e.g. every user in Global).
	const { data: users } = useUsersFiltered(undefined);
	const [userId, setUserId] = useState("");
	const [results, setResults] = useState<Record<
		FilePolicyAction,
		FileAccessTestResult
	> | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const options = useMemo(
		() =>
			(users ?? []).map((u) => ({
				value: u.id,
				label: u.name ? `${u.name} (${u.email})` : u.email,
			})),
		[users],
	);

	async function runForUser(nextUserId: string) {
		setUserId(nextUserId);
		if (!nextUserId) return;
		setLoading(true);
		setError(null);
		try {
			const next = await testAllActions({
				location,
				path,
				scope,
				userId: nextUserId,
			});
			setResults(next);
		} catch (err) {
			setError(err instanceof Error ? err.message : String(err));
			setResults(null);
		} finally {
			setLoading(false);
		}
	}

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] gap-4 overflow-auto sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>Test access</DialogTitle>
				</DialogHeader>
				<div className="space-y-1">
					<Label>Location</Label>
					<p className="rounded-md border bg-muted/30 px-2 py-1 text-sm">
						<span className="font-mono">
							{location}:{scope ?? "global"}:{path}
						</span>
					</p>
				</div>
				<div className="space-y-1">
					<Label htmlFor="test-access-user">User</Label>
					<Combobox
						options={options}
						value={userId}
						onValueChange={(value) => void runForUser(value)}
						placeholder="Select a user…"
					/>
				</div>
				{error && <p className="text-xs text-destructive">{error}</p>}
				{loading && (
					<InlineLoader label="Resolving access…" />
				)}
				{results && (
					<ul className="space-y-1">
						{ACTIONS.map((action) => {
							const r = results[action];
							return (
								<li
									key={action}
									className="flex items-center justify-between rounded-md border px-2 py-1 text-xs"
								>
									<span className="font-medium">{action}</span>
									<div className="flex items-center gap-2">
										<span className="text-muted-foreground">
											{r.allowed
												? (r.matchedRule ?? r.matchedPolicy ?? "")
												: (r.denialReason ?? "no matching rule")}
										</span>
										<Badge variant={r.allowed ? "secondary" : "destructive"}>
											{r.allowed ? "Allowed" : "Denied"}
										</Badge>
									</div>
								</li>
							);
						})}
					</ul>
				)}
			</DialogContent>
		</Dialog>
	);
}
