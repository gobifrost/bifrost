import { useMutation } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/utils";
import { refreshModelCatalog } from "@/services/aiModels";

import { useModelCatalog } from "./ProviderCatalogField";

/** Where the provider and model lists come from, and a manual refresh. */
export function ModelCatalogStatus() {
	const catalogQuery = useModelCatalog();
	const refresh = useMutation({
		mutationFn: refreshModelCatalog,
		onSuccess: () =>
			toast.success("Refreshing the model catalog", {
				description: "You'll get a notification when it finishes.",
			}),
		onError: (error) =>
			toast.error("Could not refresh the model catalog", {
				description: error instanceof Error ? error.message : undefined,
			}),
	});
	const catalog = catalogQuery.data;
	if (!catalog) return null;

	return (
		<div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
			<span data-testid="model-catalog-status">
				{catalog.providers.length} providers from the models.dev catalog
				{" · "}
				{catalog.fetched_at
					? `updated ${formatRelativeTime(catalog.fetched_at)}`
					: "bundled copy, not refreshed yet"}
			</span>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				className="min-h-11"
				disabled={refresh.isPending}
				onClick={() => refresh.mutate()}
			>
				<RefreshCw
					aria-hidden="true"
					className={`size-3.5 ${refresh.isPending ? "animate-spin motion-reduce:animate-none" : ""}`}
				/>
				Refresh catalog
			</Button>
		</div>
	);
}
