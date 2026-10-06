import { KeyRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { permissionDisplayName, WILDCARD } from "@/lib/permission-words";
import { cn } from "@/lib/utils";
import type {
	AccessGrant,
	AccessRow,
	PermissionCatalogEntry,
} from "@/services/access";

import { BRIDGE_EDGE, PermissionChip } from "./PermissionChip";
import { PlaceLabel } from "./PlaceLabel";

/** The one column heading when the only thing held is the wildcard. */
const WILDCARD_ONLY_HEADING = "Permissions";

interface Layout {
	columns: string[];
	areaOf: (grant: AccessGrant) => string | undefined;
	entryFor: (grant: AccessGrant) => PermissionCatalogEntry | undefined;
}

const isWildcard = (grant: AccessGrant) => grant.permission === WILDCARD;

/** Columns are the catalog's areas, in catalog order, that hold a grant. */
function layoutFor(
	rows: AccessRow[],
	catalog: PermissionCatalogEntry[],
): Layout {
	const byDomain = new Map(catalog.map((entry) => [entry.domain, entry]));
	const entryFor = (grant: AccessGrant) => byDomain.get(grant.domain);
	const areaOf = (grant: AccessGrant) => entryFor(grant)?.area;
	const held = new Set(
		rows.flatMap((row) =>
			row.grants.filter((grant) => !isWildcard(grant)).map(areaOf),
		),
	);
	const columns = [...new Set(catalog.map((entry) => entry.area))].filter(
		(area) => held.has(area),
	);
	return { columns, areaOf, entryFor };
}

/** A Platform Admin's wildcard: one privileged chip across the whole row. */
function WildcardChip() {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Badge asChild variant="warning">
					<button
						type="button"
						data-variant="privileged"
						className={cn(
							"h-auto min-h-6 w-full cursor-default justify-start",
							BRIDGE_EDGE,
						)}
					>
						{permissionDisplayName(WILDCARD, undefined)}
					</button>
				</Badge>
			</TooltipTrigger>
			<TooltipContent>
				Platform Admin: every permission in every organization (secret
				values excepted)
			</TooltipContent>
		</Tooltip>
	);
}

function Chips({
	grants,
	row,
	layout,
}: {
	grants: AccessGrant[];
	row: AccessRow;
	layout: Layout;
}) {
	if (grants.length === 0)
		return (
			<>
				<span aria-hidden="true" className="text-muted-foreground/60">
					—
				</span>
				<span className="sr-only">None</span>
			</>
		);
	return (
		<div className="flex flex-wrap gap-1.5">
			{grants.map((grant) => (
				<PermissionChip
					key={grant.permission}
					grant={grant}
					catalogEntry={layout.entryFor(grant)}
					place={row.place}
				/>
			))}
		</div>
	);
}

function AreaCells({ row, layout }: { row: AccessRow; layout: Layout }) {
	return layout.columns.map((area) => (
		<DataTableCell key={area} className="align-top">
			<Chips
				grants={row.grants.filter(
					(grant) =>
						!isWildcard(grant) && layout.areaOf(grant) === area,
				)}
				row={row}
				layout={layout}
			/>
		</DataTableCell>
	));
}

/**
 * A place's row. Beside the wildcard, the grants it does not cover (such as
 * reading secrets) sit in their own area columns on a second line.
 */
function PlaceRows({ row, layout }: { row: AccessRow; layout: Layout }) {
	const wildcard = row.grants.some(isWildcard);
	const explicit = row.grants.some((grant) => !isWildcard(grant));
	const place = (
		<DataTableHead
			scope="row"
			rowSpan={wildcard && explicit ? 2 : undefined}
			className="h-auto whitespace-normal py-3 align-top text-[var(--bf-reach)]"
		>
			<PlaceLabel place={row.place} />
		</DataTableHead>
	);
	if (!wildcard)
		return (
			<DataTableRow className="hover:bg-transparent">
				{place}
				<AreaCells row={row} layout={layout} />
			</DataTableRow>
		);
	return (
		<>
			<DataTableRow
				className={cn("hover:bg-transparent", explicit && "border-b-0")}
			>
				{place}
				<DataTableCell
					colSpan={Math.max(layout.columns.length, 1)}
					className="align-top"
				>
					<WildcardChip />
				</DataTableCell>
			</DataTableRow>
			{explicit && (
				<DataTableRow className="hover:bg-transparent">
					<AreaCells row={row} layout={layout} />
				</DataTableRow>
			)}
		</>
	);
}

function DesktopMap({ rows, layout }: { rows: AccessRow[]; layout: Layout }) {
	const headings =
		layout.columns.length > 0 ? layout.columns : [WILDCARD_ONLY_HEADING];
	return (
		<DataTable>
			<caption className="sr-only">Access by Place</caption>
			<DataTableHeader>
				<DataTableRow className="hover:bg-transparent">
					<DataTableHead className="w-56">Place</DataTableHead>
					{headings.map((heading) => (
						<DataTableHead key={heading}>{heading}</DataTableHead>
					))}
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{rows.map((row) => (
					<PlaceRows
						key={`${row.place.kind}:${row.place.organization_id}`}
						row={row}
						layout={layout}
					/>
				))}
			</DataTableBody>
		</DataTable>
	);
}

function MobileMap({ rows, layout }: { rows: AccessRow[]; layout: Layout }) {
	return (
		<ul
			aria-label="Access by Place"
			className="divide-y divide-border/60 overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card"
		>
			{rows.map((row) => {
				const groups = layout.columns
					.map((area) => ({
						area,
						grants: row.grants.filter(
							(grant) =>
								!isWildcard(grant) &&
								layout.areaOf(grant) === area,
						),
					}))
					.filter((group) => group.grants.length > 0);
				return (
					<li
						key={`${row.place.kind}:${row.place.organization_id}`}
						className="space-y-3 px-4 py-3"
					>
						<h3 className="text-sm font-medium text-[var(--bf-reach)]">
							<PlaceLabel place={row.place} />
						</h3>
						{row.grants.some(isWildcard) && <WildcardChip />}
						{groups.map((group) => (
							<div key={group.area} className="space-y-1.5">
								<p className="text-xs text-muted-foreground">
									{group.area}
								</p>
								<Chips
									grants={group.grants}
									row={row}
									layout={layout}
								/>
							</div>
						))}
					</li>
				);
			})}
		</ul>
	);
}

const SWATCH = "relative h-3 w-5 shrink-0 rounded-sm";

/** How to read the chips: scope by edge, privilege by tone. */
function Legend() {
	return (
		<ul
			aria-label="How to Read Permissions"
			className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground"
		>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)]`}
				/>
				Per Organization
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)] before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:rounded-l-sm before:bg-[image:var(--bf-bridge-vertical)]`}
				/>
				Platform-Wide
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-power-soft)] text-[var(--bf-power)] before:absolute before:inset-y-0.5 before:left-0 before:border-l-2 before:border-dashed before:border-current`}
				/>
				Varies by Operation
			</li>
			<li className="inline-flex items-center gap-1.5">
				<span
					aria-hidden="true"
					className={`${SWATCH} bg-[var(--bf-warning-soft)] ring-1 ring-[var(--bf-warning)]/40`}
				/>
				Privileged
			</li>
		</ul>
	);
}

/**
 * What a person can do, and where: one row per place their roles apply,
 * one column per permission area they hold. Narrow screens get one record
 * per place instead of a sideways-scrolling grid.
 */
export function AccessMap({
	rows,
	catalog,
}: {
	rows: AccessRow[];
	catalog: PermissionCatalogEntry[];
}) {
	const narrow = useMediaQuery("(max-width: 1023px)");
	if (rows.length === 0)
		return (
			<EmptyState
				icon={KeyRound}
				title="No Permissions Yet"
				description="Their roles don't grant anything anywhere. Assign a role to give them access."
			/>
		);
	const layout = layoutFor(rows, catalog);
	return (
		<div className="space-y-3">
			<Legend />
			{narrow ? (
				<MobileMap rows={rows} layout={layout} />
			) : (
				<DesktopMap rows={rows} layout={layout} />
			)}
		</div>
	);
}
