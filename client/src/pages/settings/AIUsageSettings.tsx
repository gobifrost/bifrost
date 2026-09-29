import { PricingDeleteDialog } from "@/components/ai/PricingDeleteDialog";
import { PricingEditDialog, type PricingDraft } from "@/components/ai/PricingEditDialog";
import { ModelPricingList } from "@/components/ai/ModelPricingList";
import { ModelSettingsReadError } from "@/components/ai/ModelSettingsReadError";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DollarSign, Plus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { createPricing, deletePricing, listPricing, updatePricing, type AIModelPricingListItem } from "@/services/ai-pricing";

const QUERY_KEY = ["ai", "pricing"] as const;

const EMPTY_DRAFT: PricingDraft = {
	provider: "",
	model: "",
	inputPrice: "",
	outputPrice: "",
	cacheReadPrice: "",
	cacheWritePrice: "",
};

export function AIUsageSettings() {
	const queryClient = useQueryClient();
	const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: QUERY_KEY, queryFn: listPricing });
	const [editing, setEditing] = useState<AIModelPricingListItem | null | undefined>(undefined);
	const [draft, setDraft] = useState<PricingDraft>(EMPTY_DRAFT);
	const { provider, model, inputPrice, outputPrice } = draft;
	const cachePrices = {
		cache_read_price_per_million: draft.cacheReadPrice.trim() || null,
		cache_write_price_per_million: draft.cacheWritePrice.trim() || null,
	};

	const close = () => {
		setEditing(undefined);
		setDraft(EMPTY_DRAFT);
	};
	const invalidate = () => void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
	const saveMutation = useMutation({
		mutationFn: () => editing
			? updatePricing(editing.id, { input_price_per_million: inputPrice, output_price_per_million: outputPrice, ...cachePrices })
			: createPricing({ provider: provider.trim(), model: model.trim(), input_price_per_million: inputPrice, output_price_per_million: outputPrice, ...cachePrices }),
		onSuccess: () => { invalidate(); close(); toast.success("Model pricing saved"); },
	});
	const [deleting, setDeleting] = useState<AIModelPricingListItem | null>(null);
	const headingRef = useRef<HTMLHeadingElement>(null);
	const deleteMutation = useMutation({
		mutationFn: deletePricing,
		onSuccess: () => { setDeleting(null); invalidate(); toast.success("Model pricing removed"); },
	});
	const openEdit = (item: AIModelPricingListItem) => {
		saveMutation.reset(); setEditing(item);
		setDraft({ provider: item.provider, model: item.model, inputPrice: item.input_price_per_million ?? "", outputPrice: item.output_price_per_million ?? "", cacheReadPrice: item.cache_read_price_per_million ?? "", cacheWritePrice: item.cache_write_price_per_million ?? "" });
	};

	return (
		<div className="min-w-0 w-full space-y-6">
			<div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
				<div><h2 ref={headingRef} tabIndex={-1} className="font-display text-2xl font-semibold tracking-tight outline-none focus-visible:ring-2 focus-visible:ring-ring">Usage & pricing</h2><p className="mt-1 text-sm text-muted-foreground">Maintain per-model rates used to calculate AI spend.</p></div>
				<Button className="min-h-11 w-full sm:w-auto" onClick={() => { saveMutation.reset(); setEditing(null); }}><Plus className="mr-2 h-4 w-4" />Add pricing</Button>
			</div>
			{(data?.models_without_pricing?.length ?? 0) > 0 && <div className="flex flex-wrap gap-2 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4"><span className="mr-1 text-sm font-medium">Missing pricing:</span>{data?.models_without_pricing?.map((name) => <Badge key={name} variant="outline" className="h-auto max-w-full whitespace-normal [overflow-wrap:anywhere]">{name}</Badge>)}</div>}
			<Card>
				<CardHeader><div className="flex items-start gap-3"><div className="rounded-md bg-muted p-2"><DollarSign className="h-4 w-4" /></div><div><CardTitle className="text-base">Model rates</CardTitle><CardDescription>Prices are stored per million tokens.</CardDescription></div></div></CardHeader>
				<CardContent className="min-w-0 space-y-4">
					{isError && <ModelSettingsReadError resource="model pricing" cached={Boolean(data)} pending={isFetching} onRetry={() => void refetch()} />}
					{isLoading && <p role="status" className="py-6 text-sm text-muted-foreground">Loading model pricing…</p>}
					{!isLoading && !isError && !data?.pricing?.length && <p className="py-6 text-sm text-muted-foreground">No pricing configured yet.</p>}
					{Boolean(data?.pricing?.length) && <ModelPricingList items={data?.pricing ?? []} onEdit={openEdit} onDelete={(item) => { deleteMutation.reset(); setDeleting(item); }} />}
				</CardContent>
			</Card>
			<PricingDeleteDialog pricing={deleting} pending={deleteMutation.isPending} failed={deleteMutation.isError} completed={deleteMutation.isSuccess} returnFocusRef={headingRef} onClose={() => setDeleting(null)} onConfirm={() => { if (deleting && !deleteMutation.isPending) deleteMutation.mutate(deleting.id); }} />
			<PricingEditDialog open={editing !== undefined} editing={Boolean(editing)} draft={draft} pending={saveMutation.isPending} failed={saveMutation.isError} onChange={setDraft} onClose={close} onSave={() => { if (!saveMutation.isPending) saveMutation.mutate(); }} />
		</div>
	);
}
