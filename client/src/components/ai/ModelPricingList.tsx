import { Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AIModelPricingListItem } from "@/services/ai-pricing";
import { useModelDisplayName } from "@/services/modelNames";

export function ModelPricingList({
	items,
	onEdit,
	onDelete,
}: {
	items: AIModelPricingListItem[];
	onEdit: (item: AIModelPricingListItem) => void;
	onDelete: (item: AIModelPricingListItem) => void;
}) {
	const modelName = useModelDisplayName();
	return (
		<ul
			aria-label="Model pricing"
			className="@container divide-y rounded-[var(--bf-radius-surface)] border"
		>
			{items.map((item) => (
				<li
					key={item.id}
					className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-4 p-4 @3xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] @3xl:items-center"
				>
					<div className="min-w-0">
						<Button
							type="button"
							variant="link"
							className="min-h-11 h-auto max-w-full justify-start whitespace-normal p-0 text-left font-medium [overflow-wrap:anywhere] text-foreground underline-offset-4 hover:text-foreground"
							aria-label={`Edit ${item.model}`}
							onClick={() => onEdit(item)}
						>
							{modelName(item.model)}
						</Button>
						<p className="mt-1 text-sm text-muted-foreground [overflow-wrap:anywhere]">
							{item.provider}
							{modelName(item.model) !== item.model ? ` · ${item.model}` : ""}
						</p>
					</div>
					<dl className="col-span-2 row-start-2 grid min-w-0 grid-cols-2 gap-4 text-sm @3xl:col-span-1 @3xl:col-start-2 @3xl:row-start-1">
						<div className="min-w-0">
							<dt className="text-muted-foreground">
								Input / 1M
							</dt>
							<dd className="mt-1 tabular-nums [overflow-wrap:anywhere]">
								{item.input_price_per_million == null
									? "Not set"
									: `$${item.input_price_per_million}`}
							</dd>
						</div>
						<div className="min-w-0">
							<dt className="text-muted-foreground">
								Output / 1M
							</dt>
							<dd className="mt-1 tabular-nums [overflow-wrap:anywhere]">
								{item.output_price_per_million == null
									? "Not set"
									: `$${item.output_price_per_million}`}
							</dd>
						</div>
					</dl>
					<div
						role="group"
						aria-label={`Actions for ${item.model}`}
						className="col-start-2 row-start-1 flex items-start justify-end @3xl:col-start-3 @3xl:items-center"
					>
						<RecordActionsMenu
							label={`More actions for ${item.model}`}
						>
							<DropdownMenuItem
								className="min-h-11"
								onSelect={() => onEdit(item)}
							>
								<Pencil className="size-4" />
								Edit
							</DropdownMenuItem>
							<DropdownMenuItem
								variant="destructive"
								className="min-h-11"
								onSelect={() => onDelete(item)}
							>
								<Trash2 className="size-4" />
								Delete
							</DropdownMenuItem>
						</RecordActionsMenu>
					</div>
				</li>
			))}
		</ul>
	);
}
