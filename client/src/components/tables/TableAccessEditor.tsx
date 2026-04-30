import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { MultiCombobox } from "@/components/ui/multi-combobox";
import type { components } from "@/lib/v1";

type TableAccess = components["schemas"]["TableAccess"];
type TableAccessScopeCRUD = components["schemas"]["TableAccessScopeCRUD"];
type TableAccessRoleScope = components["schemas"]["TableAccessRoleScope"];

const EMPTY_CRUD: TableAccessScopeCRUD = {
	read: false,
	create: false,
	update: false,
	delete: false,
};

const ACTIONS: Array<keyof TableAccessScopeCRUD> = [
	"read",
	"create",
	"update",
	"delete",
];

interface ScopeCardProps {
	title: string;
	scope: keyof TableAccess;
	flags: TableAccessScopeCRUD;
	onToggle: (action: keyof TableAccessScopeCRUD) => void;
}

function ScopeCard({ title, scope, flags, onToggle }: ScopeCardProps) {
	return (
		<Card>
			<CardHeader className="pb-2 pt-4 px-4">
				<CardTitle className="text-sm font-medium">{title}</CardTitle>
			</CardHeader>
			<CardContent className="px-4 pb-4">
				<div className="grid grid-cols-2 gap-2">
					{ACTIONS.map((action) => {
						const id = `${scope}-${action}`;
						const label = `${title} — ${action.charAt(0).toUpperCase()}${action.slice(1)}`;
						return (
							<div key={action} className="flex items-center gap-2">
								<Checkbox
									id={id}
									checked={flags[action]}
									onCheckedChange={() => onToggle(action)}
									aria-label={label}
								/>
								<Label
									htmlFor={id}
									className="text-sm font-normal capitalize cursor-pointer"
								>
									{action}
								</Label>
							</div>
						);
					})}
				</div>
			</CardContent>
		</Card>
	);
}

export interface TableAccessEditorProps {
	value: TableAccess | null;
	roles: Array<{ id: string; name: string }>;
	onChange: (next: TableAccess) => void;
}

export function TableAccessEditor({
	value,
	roles,
	onChange,
}: TableAccessEditorProps) {
	const v: Required<TableAccess> = {
		everyone: value?.everyone ?? { ...EMPTY_CRUD },
		role: value?.role ?? { ...EMPTY_CRUD, roles: [] },
		creator: value?.creator ?? { ...EMPTY_CRUD },
	};

	function updateScope<K extends keyof TableAccess>(
		scope: K,
		patch: Partial<TableAccess[K]>,
	) {
		onChange({ ...v, [scope]: { ...v[scope], ...patch } });
	}

	function toggleFlag(
		scope: "everyone" | "creator",
		action: keyof TableAccessScopeCRUD,
	) {
		updateScope(scope, { [action]: !v[scope][action] } as Partial<
			TableAccessScopeCRUD
		>);
	}

	function toggleRoleFlag(action: keyof TableAccessScopeCRUD) {
		updateScope("role", { [action]: !v.role[action] } as Partial<
			TableAccessRoleScope
		>);
	}

	const roleOptions = roles.map((r) => ({ value: r.id, label: r.name }));
	const selectedRoleIds = v.role.roles ?? [];

	return (
		<div className="grid gap-3">
			<ScopeCard
				title="Everyone"
				scope="everyone"
				flags={v.everyone}
				onToggle={(action) => toggleFlag("everyone", action)}
			/>

			<Card>
				<CardHeader className="pb-2 pt-4 px-4">
					<CardTitle className="text-sm font-medium">Role</CardTitle>
				</CardHeader>
				<CardContent className="px-4 pb-4 space-y-3">
					<div>
						<Label className="text-xs text-muted-foreground mb-1.5 block">
							Allowed roles
						</Label>
						<MultiCombobox
							options={roleOptions}
							value={selectedRoleIds}
							onValueChange={(ids) =>
								updateScope("role", { roles: ids })
							}
							placeholder="Select roles..."
							searchPlaceholder="Search roles..."
							emptyText="No roles found."
						/>
					</div>
					<div className="grid grid-cols-2 gap-2">
						{ACTIONS.map((action) => {
							const id = `role-${action}`;
							const label = `Role — ${action.charAt(0).toUpperCase()}${action.slice(1)}`;
							return (
								<div key={action} className="flex items-center gap-2">
									<Checkbox
										id={id}
										checked={v.role[action]}
										onCheckedChange={() => toggleRoleFlag(action)}
										aria-label={label}
									/>
									<Label
										htmlFor={id}
										className="text-sm font-normal capitalize cursor-pointer"
									>
										{action}
									</Label>
								</div>
							);
						})}
					</div>
				</CardContent>
			</Card>

			<ScopeCard
				title="Creator"
				scope="creator"
				flags={v.creator}
				onToggle={(action) => toggleFlag("creator", action)}
			/>
		</div>
	);
}
