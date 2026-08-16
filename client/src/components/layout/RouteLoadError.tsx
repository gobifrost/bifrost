import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useRouteError } from "react-router-dom";

import { Button } from "@/components/ui/button";

export function RouteLoadError() {
	const error = useRouteError();
	const isAgent = window.location.pathname.startsWith("/agents/");
	const entity = isAgent ? "agent" : "application";
	const returnPath = isAgent ? "/agents" : "/apps";
	const detail =
		error instanceof Error
			? error.message
			: `The ${entity} could not be loaded.`;

	return (
		<div className="flex h-full min-h-[420px] w-full items-center justify-center p-6">
			<div className="w-full max-w-md text-center">
				<div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-destructive/10 text-destructive">
					<AlertTriangle aria-hidden="true" className="h-5 w-5" />
				</div>
				<h1 className="mt-4 text-xl font-semibold">
					Couldn&apos;t open this {entity}
				</h1>
				<p className="mt-2 text-sm text-muted-foreground">{detail}</p>
				<div className="mt-5 flex justify-center gap-2">
					<Button variant="outline" asChild>
						<Link to={returnPath}>
							<ArrowLeft className="h-4 w-4" /> Back
						</Link>
					</Button>
					<Button onClick={() => window.location.reload()}>
						<RefreshCw className="h-4 w-4" /> Try again
					</Button>
				</div>
			</div>
		</div>
	);
}
