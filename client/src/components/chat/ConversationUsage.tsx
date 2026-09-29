import { BarChart3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { useConversationStats } from "@/hooks/useChat";
import { useModelDisplayName } from "@/services/modelNames";

export function ConversationUsage({ stats }: { stats: NonNullable<ReturnType<typeof useConversationStats>> }) {
	const modelName = useModelDisplayName();
	return (
		<Popover>
			<PopoverTrigger asChild>
				<Button type="button" variant="ghost" className="min-h-11 shrink-0 gap-2" aria-label="Conversation usage"><BarChart3 className="size-4" /><span className="hidden sm:inline">Usage</span></Button>
			</PopoverTrigger>
			<PopoverContent align="end" className="w-72 max-w-[calc(100vw-2rem)] space-y-3">
				<h2 className="font-semibold">Conversation usage</h2>
				<dl className="space-y-3 text-sm">
					{stats.model && <div><dt className="text-muted-foreground">Model</dt><dd className="text-sm [overflow-wrap:anywhere]" title={stats.model}>{modelName(stats.model)}</dd></div>}
					{[["Input tokens", stats.totalInputTokens], ["Output tokens", stats.totalOutputTokens], ["Total tokens", stats.totalTokens]].map(([label, value]) => <div key={label} className="flex flex-wrap justify-between gap-2"><dt className="text-muted-foreground">{label}</dt><dd className="font-mono">{value.toLocaleString()}</dd></div>)}
					{stats.estimatedCostUsd !== null && <div className="flex flex-wrap justify-between gap-2"><dt className="text-muted-foreground">Estimated cost</dt><dd className="font-mono">${stats.estimatedCostUsd.toFixed(stats.estimatedCostUsd < 0.01 ? 4 : 2)}</dd></div>}
				</dl>
			</PopoverContent>
		</Popover>
	);
}
