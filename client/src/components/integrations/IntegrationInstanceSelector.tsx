import { ChevronDown, Plus, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface IntegrationInstanceOption {
	id: string;
	definition_id: string;
	key: string;
	name: string;
	is_default: boolean;
	authentication_method: "config" | "oauth";
	has_oauth_config: boolean;
}

export function IntegrationInstanceSelector({
	instances,
	selectedInstanceId,
	onSelect,
	onAddInstance,
	onManageInstances,
	canManageInstances,
	disabled = false,
}: {
	instances: IntegrationInstanceOption[];
	selectedInstanceId: string;
	onSelect: (instanceId: string) => void;
	onAddInstance: () => void;
	onManageInstances: () => void;
	canManageInstances: boolean;
	disabled?: boolean;
}) {
	const selected = instances.find((instance) => instance.id === selectedInstanceId);

	if (!selected) return null;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className="max-w-full"
					aria-label={`Selected instance: ${selected.is_default ? "Default" : selected.name}`}
					disabled={disabled}
				>
					<span className="truncate">{selected.is_default ? "Default" : selected.name}</span>
					<ChevronDown className="size-3 shrink-0" aria-hidden="true" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="start">
				<DropdownMenuLabel>Connection Instance</DropdownMenuLabel>
				<DropdownMenuRadioGroup
					value={selected.id}
					onValueChange={onSelect}
				>
					{instances.map((instance) => (
						<DropdownMenuRadioItem key={instance.id} value={instance.id} disabled={disabled}>
							{instance.is_default ? "Default" : instance.name}
						</DropdownMenuRadioItem>
					))}
				</DropdownMenuRadioGroup>
				{canManageInstances && (
					<>
						<DropdownMenuSeparator />
						<DropdownMenuItem onSelect={onAddInstance}>
							<Plus />
							Add Instance
						</DropdownMenuItem>
						<DropdownMenuItem onSelect={onManageInstances}>
							<Settings2 />
							Manage Instances
						</DropdownMenuItem>
					</>
				)}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
