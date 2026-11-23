import { Maximize, Minus, Plus, LocateFixed } from "lucide-react";
import { useNodesStore, useReactFlow, useViewportStore } from "@xyflow/react";
import { Button } from "@/components/ui/button";

export function DependencyGraphControls() {
	const { zoomIn, zoomOut, fitView } = useReactFlow();
	const zoom = useViewportStore((state) => state.transform[2]);
	const nodes = useNodesStore((state) => state.nodes);
	const selectedNodes = nodes.filter((node) => node.selected);
	const zoomPercent = Math.round(zoom * 100);

	return (
		<div
			role="toolbar"
			aria-label="Graph view"
			className="flex shrink-0 flex-wrap items-start gap-2 border-t bg-background p-2 sm:items-center sm:justify-between"
		>
			<div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-11"
					aria-label="Zoom in"
					onClick={() => void zoomIn({ duration: 0 })}
				>
					<Plus className="size-4" />
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-11"
					aria-label="Zoom out"
					onClick={() => void zoomOut({ duration: 0 })}
				>
					<Minus className="size-4" />
				</Button>

				<Button
					type="button"
					variant="ghost"
					className="size-11 sm:w-auto"
					aria-label="Fit graph"
					title="Fit graph"
					onClick={() =>
						void fitView({
							padding: "8.34%",
							maxZoom: 1.5,
							duration: 0,
						})
					}
				>
					<Maximize className="size-4" />
					<span className="hidden sm:inline">Fit graph</span>
				</Button>
				{selectedNodes.length > 0 && (
					<Button
						type="button"
						variant="ghost"
						className="min-h-11 w-full sm:w-auto"
						aria-label={`Focus ${selectedNodes.length} selected node${selectedNodes.length === 1 ? "" : "s"}`}
						onClick={() =>
							void fitView({
								nodes: selectedNodes,
								padding: "9.68%",
								maxZoom: 1.5,
								duration: 0,
							})
						}
					>
						<LocateFixed className="size-4" />
						Focus selection
					</Button>
				)}
			</div>
			<div className="flex items-center gap-2 text-xs text-muted-foreground">
				<span className="inline-flex min-h-11 items-center rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/40 px-2.5 font-medium text-foreground">
					{zoomPercent}%
				</span>
				<span className="hidden lg:inline">
					Pinch or scroll to zoom
				</span>
			</div>
		</div>
	);
}
