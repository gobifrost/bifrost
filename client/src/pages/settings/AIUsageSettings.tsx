import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DollarSign, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { createPricing, deletePricing, listPricing, updatePricing, type AIModelPricingListItem } from "@/services/ai-pricing";

const QUERY_KEY = ["ai", "pricing"] as const;

export function AIUsageSettings() {
	const queryClient = useQueryClient();
	const { data, isLoading } = useQuery({ queryKey: QUERY_KEY, queryFn: listPricing });
	const [editing, setEditing] = useState<AIModelPricingListItem | null | undefined>(undefined);
	const [provider, setProvider] = useState("");
	const [model, setModel] = useState("");
	const [inputPrice, setInputPrice] = useState("");
	const [outputPrice, setOutputPrice] = useState("");

	const close = () => {
		setEditing(undefined);
		setProvider(""); setModel(""); setInputPrice(""); setOutputPrice("");
	};
	const invalidate = () => void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
	const saveMutation = useMutation({
		mutationFn: () => editing
			? updatePricing(editing.id, { input_price_per_million: inputPrice, output_price_per_million: outputPrice })
			: createPricing({ provider: provider.trim(), model: model.trim(), input_price_per_million: inputPrice, output_price_per_million: outputPrice }),
		onSuccess: () => { invalidate(); close(); toast.success("Model pricing saved"); },
		onError: (error) => toast.error(error instanceof Error ? error.message : "Could not save pricing"),
	});
	const deleteMutation = useMutation({
		mutationFn: deletePricing,
		onSuccess: () => { invalidate(); toast.success("Model pricing removed"); },
		onError: (error) => toast.error(error instanceof Error ? error.message : "Could not remove pricing"),
	});
	const openEdit = (item: AIModelPricingListItem) => {
		setEditing(item); setProvider(item.provider); setModel(item.model);
		setInputPrice(item.input_price_per_million ?? ""); setOutputPrice(item.output_price_per_million ?? "");
	};

	return (
		<div className="max-w-5xl space-y-6">
			<div className="flex items-start justify-between gap-4">
				<div><h2 className="text-2xl font-semibold tracking-tight">Usage & pricing</h2><p className="mt-1 text-sm text-muted-foreground">Maintain per-model rates used to calculate AI spend.</p></div>
				<Button onClick={() => setEditing(null)}><Plus className="mr-2 h-4 w-4" />Add pricing</Button>
			</div>
			{(data?.models_without_pricing?.length ?? 0) > 0 && <div className="flex flex-wrap gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"><span className="mr-1 text-sm font-medium">Missing pricing:</span>{data?.models_without_pricing?.map((name) => <Badge key={name} variant="outline">{name}</Badge>)}</div>}
			<Card>
				<CardHeader><div className="flex items-start gap-3"><div className="rounded-md bg-muted p-2"><DollarSign className="h-4 w-4" /></div><div><CardTitle className="text-base">Model rates</CardTitle><CardDescription>Prices are stored per million tokens.</CardDescription></div></div></CardHeader>
				<CardContent>
					<div className="overflow-auto rounded-md border"><Table><TableHeader><TableRow><TableHead>Provider</TableHead><TableHead>Model</TableHead><TableHead>Input</TableHead><TableHead>Output</TableHead><TableHead className="w-24"><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader><TableBody>
						{isLoading && <TableRow><TableCell colSpan={5} className="h-24 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin" /></TableCell></TableRow>}
						{!isLoading && (data?.pricing?.length ?? 0) === 0 && <TableRow><TableCell colSpan={5} className="h-24 text-center text-muted-foreground">No pricing configured yet.</TableCell></TableRow>}
						{data?.pricing?.map((item) => <TableRow key={item.id}><TableCell className="font-medium">{item.provider}</TableCell><TableCell>{item.model}</TableCell><TableCell>${item.input_price_per_million ?? "—"}</TableCell><TableCell>${item.output_price_per_million ?? "—"}</TableCell><TableCell><div className="flex justify-end"><Button variant="ghost" size="icon" aria-label={`Edit ${item.model}`} onClick={() => openEdit(item)}><Pencil className="h-4 w-4" /></Button><Button variant="ghost" size="icon" aria-label={`Delete ${item.model}`} onClick={() => deleteMutation.mutate(item.id)}><Trash2 className="h-4 w-4" /></Button></div></TableCell></TableRow>)}
					</TableBody></Table></div>
				</CardContent>
			</Card>
			<Dialog open={editing !== undefined} onOpenChange={(open) => { if (!open) close(); }}><DialogContent><DialogHeader><DialogTitle>{editing ? "Edit model pricing" : "Add model pricing"}</DialogTitle><DialogDescription>Enter token prices in US dollars per million tokens.</DialogDescription></DialogHeader><div className="grid gap-4 py-2"><div className="grid gap-2 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="pricing-provider">Provider</Label><Input id="pricing-provider" value={provider} onChange={(event) => setProvider(event.target.value)} disabled={Boolean(editing)} /></div><div className="space-y-2"><Label htmlFor="pricing-model">Model</Label><Input id="pricing-model" value={model} onChange={(event) => setModel(event.target.value)} disabled={Boolean(editing)} /></div></div><div className="grid gap-2 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="pricing-input">Input price</Label><Input id="pricing-input" inputMode="decimal" value={inputPrice} onChange={(event) => setInputPrice(event.target.value)} /></div><div className="space-y-2"><Label htmlFor="pricing-output">Output price</Label><Input id="pricing-output" inputMode="decimal" value={outputPrice} onChange={(event) => setOutputPrice(event.target.value)} /></div></div></div><DialogFooter><Button variant="outline" onClick={close}>Cancel</Button><Button onClick={() => saveMutation.mutate()} disabled={!provider.trim() || !model.trim() || !inputPrice || !outputPrice || saveMutation.isPending}>{saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Save pricing</Button></DialogFooter></DialogContent></Dialog>
		</div>
	);
}
