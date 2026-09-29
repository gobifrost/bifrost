import { Pencil, RefreshCw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AIProviderConnection, ModelCatalogProvider } from "@/services/aiModels";

import { providerLabel } from "./providerOptions";

export function ProviderConnectionCard({ provider, catalogProvider, testing, onEdit, onTest, onDelete }: {
	provider: AIProviderConnection;
	/** The catalog entry this connection belongs to; absent for custom endpoints. */
	catalogProvider: ModelCatalogProvider | undefined;
	testing: boolean;
	onEdit: () => void;
	onTest: () => void;
	onDelete: () => void;
}) {
	return <Card className="min-w-0" size="sm">
		<CardHeader>
			<CardTitle className="min-w-0 text-base [overflow-wrap:anywhere]">{provider.name}</CardTitle>
			<CardDescription className="min-w-0 [overflow-wrap:anywhere]">{provider.endpoint || "Default provider endpoint"}</CardDescription>
		</CardHeader>
		<CardContent className="space-y-4">
			<div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
				<Badge variant="secondary">{catalogProvider?.name ?? `Custom · ${providerLabel(provider.provider)}`}</Badge>
				{catalogProvider && !catalogProvider.native && <Badge variant="outline" title="Uses a native adapter; not tested by Bifrost directly">Community</Badge>}
				<span>{provider.profile_count} {provider.profile_count === 1 ? "profile" : "profiles"}</span>
				<span>Key {provider.api_key_set ? "saved" : "missing"}</span>
			</div>
			<div role="group" aria-label={`Actions for ${provider.name}`} className="flex flex-wrap gap-2 border-t pt-3">
				<Button type="button" variant="outline" className="min-h-11" aria-label={`Edit ${provider.name}`} onClick={onEdit}><Pencil aria-hidden="true" className="size-4" />Edit</Button>
				<Button type="button" variant="outline" className="min-h-11" aria-label={`Test ${provider.name}`} disabled={testing} onClick={onTest}><RefreshCw aria-hidden="true" className={`size-4 ${testing ? "animate-spin motion-reduce:animate-none" : ""}`} />{testing ? "Testing…" : "Test"}</Button>
				<Button type="button" variant="ghost" className="min-h-11" aria-label={`Delete ${provider.name}`} onClick={onDelete}><Trash2 aria-hidden="true" className="size-4" />Delete</Button>
			</div>
		</CardContent>
	</Card>;
}
