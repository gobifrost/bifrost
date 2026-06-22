import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { effectiveAccess, type FilePolicy } from "@/services/filePolicies";

interface EffectiveAccessPanelProps {
	location: string;
	scope: string | null;
	path: string | null;
	onOpenTest: () => void;
	onManagePolicy: () => void;
}

export function EffectiveAccessPanel({
	location,
	scope,
	path,
	onOpenTest,
	onManagePolicy,
}: EffectiveAccessPanelProps) {
	const [policies, setPolicies] = useState<FilePolicy[]>([]);
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);

	useEffect(() => {
		let cancelled = false;
		void (async () => {
			if (path === null) {
				setPolicies([]);
				return;
			}
			setLoading(true);
			try {
				const result = await effectiveAccess(location, path, scope);
				if (!cancelled) {
					setPolicies(result);
					setError(null);
				}
			} catch (err) {
				if (!cancelled)
					setError(err instanceof Error ? err.message : String(err));
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [location, scope, path]);

	return (
		<section className="flex h-full min-h-0 flex-col">
			<div className="flex items-center justify-between border-b px-3 py-2">
				<div className="flex items-center gap-2">
					<ShieldCheck className="h-4 w-4 text-muted-foreground" />
					<h2 className="text-sm font-semibold">Effective Access</h2>
				</div>
				<div className="flex gap-1">
					<Button type="button" variant="outline" size="xs" onClick={onManagePolicy}>
						Manage policy
					</Button>
					<Button
						type="button"
						size="xs"
						onClick={onOpenTest}
						disabled={path === null}
					>
						Test access
					</Button>
				</div>
			</div>
			<div className="min-h-0 flex-1 overflow-auto p-3 text-xs">
				{path === null && (
					<p className="text-muted-foreground">
						Select an item to see what governs it.
					</p>
				)}
				{loading && <p className="text-muted-foreground">Resolving…</p>}
				{error && <p className="text-destructive">{error}</p>}
				{path !== null && !loading && !error && policies.length === 0 && (
					<p className="text-muted-foreground">
						No policy governs this path (default deny).
					</p>
				)}
				<ul className="space-y-2">
					{policies.map((policy, index) => (
						<li
							key={policy.id ?? `${policy.location}:${policy.path}`}
							className="rounded-md border bg-muted/30 p-2"
						>
							<div className="flex items-center justify-between">
								<span className="font-mono text-[11px]">
									{policy.path || "(root)"}
								</span>
								{index === 0 && <Badge variant="secondary">winning</Badge>}
							</div>
							<ul className="mt-1 space-y-0.5">
								{policy.policies.policies.map((rule) => (
									<li key={rule.name} className="text-muted-foreground">
										<span className="font-medium text-foreground">
											{rule.name}
										</span>{" "}
										→ {rule.actions.join(", ")}
									</li>
								))}
							</ul>
						</li>
					))}
				</ul>
			</div>
		</section>
	);
}
