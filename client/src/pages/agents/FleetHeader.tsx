import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { History, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

export function FleetHeader({
	title,
	agentLabel,
	total,
	active,
	actions,
}: {
	title: string;
	agentLabel: string;
	total: number;
	active: number;
	actions?: ReactNode;
}) {
	return (
		<header className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
			<div className="min-w-0">
				<h1 className="font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
					{title}
				</h1>
				<p className="mt-1 text-sm text-muted-foreground">
					{total} total · {active} active · last 7 days
				</p>
			</div>
			<div className="flex flex-wrap items-center gap-2 [&>a]:min-h-11 [&>button]:min-h-11">
				<Button
					asChild
					variant="outline"
					className="flex-1 sm:flex-none"
				>
					<Link to="/agents/quality">
						<Sparkles aria-hidden="true" className="size-4" />
						Quality
					</Link>
				</Button>
				<Button
					asChild
					variant="outline"
					className="flex-1 sm:flex-none"
				>
					<Link to="/history?type=agents">
						<History aria-hidden="true" className="size-4" />
						All runs
					</Link>
				</Button>
				{actions}
				<Button asChild className="flex-1 sm:flex-none">
					<Link to="/agents/new">
						<Plus aria-hidden="true" className="size-4" />
						New {agentLabel}
					</Link>
				</Button>
			</div>
		</header>
	);
}
