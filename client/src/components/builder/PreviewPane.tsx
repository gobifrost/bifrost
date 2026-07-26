/**
 * Builder preview pane.
 *
 * Generated apps render on a separate app origin. Until that origin is
 * configured there is nothing safe to frame, so the pane shows an explicit
 * empty state rather than a broken iframe.
 */

import { AlertTriangle, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface PreviewPaneProps {
	/** App-host origin, or null when the deployment has no distinct origin yet. */
	appOrigin: string | null;
	route: string;
	onRouteChange: (route: string) => void;
	onReload: () => void;
	/** Source is ahead of the deployed revision — preview is last-good. */
	isStale: boolean;
}

export function PreviewPane({
	appOrigin,
	route,
	onRouteChange,
	onReload,
	isStale,
}: PreviewPaneProps) {
	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex items-center gap-2 border-b p-2">
				<Input
					value={route}
					aria-label="Preview route"
					placeholder="/"
					className="h-8 font-mono text-xs"
					onChange={(event) => onRouteChange(event.target.value)}
				/>
				<Button
					variant="ghost"
					size="icon"
					className="h-8 w-8 shrink-0"
					aria-label="Reload preview"
					disabled={!appOrigin}
					onClick={onReload}
				>
					<RefreshCw className="h-4 w-4" />
				</Button>
				{isStale && (
					<Badge
						variant="outline"
						className="shrink-0 gap-1 border-amber-500/50 text-amber-600 dark:text-amber-400"
						data-testid="stale-preview-badge"
					>
						<AlertTriangle className="h-3 w-3" />
						Stale
					</Badge>
				)}
			</div>

			<div className="min-h-0 flex-1 overflow-auto bg-muted/30">
				{appOrigin ? (
					<iframe
						title="App preview"
						data-testid="preview-frame"
						src={`${appOrigin}${route}`}
						className="h-full w-full border-0 bg-background"
						sandbox="allow-scripts allow-forms allow-same-origin"
					/>
				) : (
					<div
						className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center"
						data-testid="preview-unavailable"
					>
						<AlertTriangle className="h-6 w-6 text-muted-foreground" />
						<p className="text-sm font-medium">Preview unavailable</p>
						<p className="max-w-sm text-sm text-muted-foreground">
							App origin is not configured. A platform administrator must
							configure a distinct app origin before generated apps can be
							previewed.
						</p>
					</div>
				)}
			</div>
		</div>
	);
}
