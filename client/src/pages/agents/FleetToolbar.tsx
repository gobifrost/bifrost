import { Search, LayoutGrid, List } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { cn } from "@/lib/utils";

export function FleetToolbar({
	agentLabel,
	query,
	filterOrgId,
	showInactive,
	view,
	canFilterOrganizations,
	loadOrganizations,
	knownOrganizations,
	onQueryChange,
	onOrganizationChange,
	onInactiveChange,
	onViewChange,
}: {
	agentLabel: string;
	query: string;
	filterOrgId: string | null | undefined;
	showInactive: boolean;
	view: "grid" | "table";
	canFilterOrganizations: boolean;
	loadOrganizations: boolean;
	knownOrganizations: readonly { id: string; name: string }[];
	onQueryChange: (value: string) => void;
	onOrganizationChange: (value: string | null | undefined) => void;
	onInactiveChange: (value: boolean) => void;
	onViewChange: (value: "grid" | "table") => void;
}) {
	return (
		<div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
			<div className="flex flex-1 flex-col gap-3 md:flex-row md:flex-wrap md:items-center">
				<div className="relative w-full sm:max-w-md sm:flex-1">
					<Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
					<Input
						aria-label={`Search ${agentLabel}`}
						placeholder={`Search ${agentLabel}...`}
						value={query}
						onChange={(e) => onQueryChange(e.target.value)}
						className="min-h-11 pl-9 text-sm"
					/>
				</div>
				{canFilterOrganizations && (
					<div className="w-full sm:w-64 [&_button[role=combobox]]:min-h-11">
						<OrganizationSelect
							value={filterOrgId}
							onChange={onOrganizationChange}
							showAll
							showGlobal
							loadOrganizations={loadOrganizations}
							knownOrganizations={knownOrganizations}
							placeholder="All organizations"
						/>
					</div>
				)}
				<div className="flex items-center gap-2 sm:ml-auto">
					<Switch
						id="show-inactive"
						checked={showInactive}
						onCheckedChange={onInactiveChange}
					/>
					<Label
						htmlFor="show-inactive"
						className="min-h-11 cursor-pointer whitespace-nowrap text-sm text-muted-foreground"
					>
						Show Inactive
					</Label>
				</div>
			</div>
			<div className="hidden items-center rounded-[var(--bf-radius-control)] bg-muted p-1 lg:inline-flex">
				<button
					type="button"
					aria-label="Grid view"
					aria-pressed={view === "grid"}
					onClick={() => onViewChange("grid")}
					className={cn(
						"inline-flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-control)] px-3 text-sm transition-colors motion-reduce:transition-none",
						view === "grid"
							? "bg-card text-foreground shadow-sm"
							: "text-muted-foreground hover:text-foreground",
					)}
				>
					<LayoutGrid className="h-3 w-3" /> Grid
				</button>
				<button
					type="button"
					aria-label="Table view"
					aria-pressed={view === "table"}
					onClick={() => onViewChange("table")}
					className={cn(
						"inline-flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-control)] px-3 text-sm transition-colors motion-reduce:transition-none",
						view === "table"
							? "bg-card text-foreground shadow-sm"
							: "text-muted-foreground hover:text-foreground",
					)}
				>
					<List className="h-3 w-3" /> Table
				</button>
			</div>
		</div>
	);
}
