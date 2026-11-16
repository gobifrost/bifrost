import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { KeyRound, Plus, RefreshCw } from "lucide-react";

import {
	CredentialExpiryBadge,
	ProvisioningStatusBadge,
} from "@/components/identity-providers/IdentityProviderBadges";
import {
	NewIdentityProviderDialog,
	type IdentityProviderCreated,
} from "@/components/identity-providers/NewIdentityProviderDialog";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { UsersViewTabs } from "@/components/users/UsersViewTabs";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";
import {
	IDENTITY_PROVIDERS_PATH,
	mappedGroupsLabel,
	useIdentityProviders,
	vendorLabel,
	type IdentityProvider,
} from "@/services/identity-providers";

function matches(provider: IdentityProvider, search: string): boolean {
	const term = search.trim().toLowerCase();
	return (
		!term ||
		[
			provider.name,
			provider.organization_name,
			vendorLabel(provider.vendor),
			...provider.verified_domains,
		].some((value) => value.toLowerCase().includes(term))
	);
}

function Domains({ domains }: { domains: string[] }) {
	if (domains.length === 0) {
		return <span className="text-muted-foreground">None Verified</span>;
	}
	return (
		<span className="[overflow-wrap:anywhere]">
			{domains.map((domain) => `@${domain}`).join(", ")}
		</span>
	);
}

/**
 * Each organization's identity provider connections: the directories that
 * provision its users and map their groups to roles.
 */
export function IdentityProviders() {
	const navigate = useNavigate();
	const isNarrow = useMediaQuery("(max-width: 1023px)");
	const authorization = useAuthorization();
	const [search, setSearch] = useState("");
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const query = useIdentityProviders();
	const providers = useMemo(
		() => (query.data ?? []).filter((p) => matches(p, search)),
		[query.data, search],
	);
	const detailPath = (provider: Pick<IdentityProvider, "id">) =>
		`${IDENTITY_PROVIDERS_PATH}/${provider.id}`;
	const onCreated = (created: IdentityProviderCreated) =>
		navigate(detailPath(created.identity_provider), {
			state: { issuedToken: created.token },
		});

	return (
		<PageWorkspace className="mx-auto max-w-7xl">
			<ListPageHeader
				title="Identity Providers"
				titleSlot={<UsersViewTabs />}
				description="Directories that provision each organization's users over SCIM and map their groups to roles."
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => query.refetch()}
							title="Refresh"
							aria-label="Refresh identity providers"
							className="h-11 w-11 lg:h-9 lg:w-9"
							disabled={query.isFetching}
						>
							<RefreshCw
								className={cn(
									"h-4 w-4",
									query.isFetching &&
										"animate-spin motion-reduce:animate-none",
								)}
							/>
						</Button>
						{authorization.canAnywhere(
							"identityproviders.readwrite",
						) && (
							<Button
								className="min-h-11 lg:min-h-0"
								onClick={() => setIsCreateOpen(true)}
							>
								<Plus className="mr-1.5 h-4 w-4" />
								Add Provider
							</Button>
						)}
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={search}
					onChange={setSearch}
					placeholder="Search by name, organization, provider or domain..."
					className="w-full sm:flex-1"
				/>
			</ListToolbar>

			{query.isError && (
				<ListLoadError
					resource="identity providers"
					hasCachedData={!!query.data}
					isRetrying={query.isFetching}
					onRetry={() => void query.refetch()}
				/>
			)}
			<PageScrollArea
				aria-label="Identity providers list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
				aria-busy={query.isFetching}
			>
				{query.isLoading ? (
					<div
						className="space-y-2"
						role="status"
						aria-label="Loading identity providers"
					>
						{[...Array(3)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : query.isError && !query.data ? null : providers.length ===
				  0 ? (
					<EmptyState
						icon={KeyRound}
						title={
							search
								? "No Matching Identity Providers"
								: "No Identity Providers"
						}
						description={
							search
								? "Try a different name, organization, provider or domain."
								: "Add a provider to provision an organization's users from Entra ID, Okta, JumpCloud or Google Workspace."
						}
					/>
				) : isNarrow ? (
					<ul
						aria-label="Identity providers"
						className="divide-y overflow-hidden rounded-lg border bg-card"
					>
						{providers.map((provider) => (
							<li key={provider.id} className="space-y-3 p-4">
								<div className="flex flex-wrap items-center gap-2">
									<Link
										to={detailPath(provider)}
										className="inline-flex min-h-11 items-center font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
									>
										{provider.name}
									</Link>
									<ProvisioningStatusBadge
										enabled={provider.is_enabled}
									/>
								</div>
								<dl className="grid grid-cols-2 gap-3 text-sm">
									<div>
										<dt className="text-xs text-muted-foreground">
											Organization
										</dt>
										<dd className="mt-1">
											{provider.organization_name}
										</dd>
									</div>
									<div>
										<dt className="text-xs text-muted-foreground">
											Provider
										</dt>
										<dd className="mt-1">
											{vendorLabel(provider.vendor)}
										</dd>
									</div>
									<div className="col-span-2">
										<dt className="text-xs text-muted-foreground">
											Email Domains
										</dt>
										<dd className="mt-1">
											<Domains
												domains={
													provider.verified_domains
												}
											/>
										</dd>
									</div>
									<div>
										<dt className="text-xs text-muted-foreground">
											Token Expiry
										</dt>
										<dd className="mt-1">
											<CredentialExpiryBadge
												token={provider.token}
											/>
										</dd>
									</div>
									<div>
										<dt className="text-xs text-muted-foreground">
											Users · Groups Mapped
										</dt>
										<dd className="mt-1 tabular-nums">
											{provider.user_count} ·{" "}
											{mappedGroupsLabel(provider)}
										</dd>
									</div>
								</dl>
							</li>
						))}
					</ul>
				) : (
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead className="min-w-48">
									Name
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Provider
								</DataTableHead>
								<DataTableHead>Email Domains</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Provisioning
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Token Expiry
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Users
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Groups Mapped
								</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{providers.map((provider) => (
								<DataTableRow
									key={provider.id}
									href={detailPath(provider)}
									onClick={() =>
										navigate(detailPath(provider))
									}
								>
									<DataTableCell className="min-w-48 [overflow-wrap:anywhere]">
										<Link
											to={detailPath(provider)}
											className="font-medium hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
										>
											{provider.name}
										</Link>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{provider.organization_name}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{vendorLabel(provider.vendor)}
									</DataTableCell>
									<DataTableCell>
										<Domains
											domains={provider.verified_domains}
										/>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<ProvisioningStatusBadge
											enabled={provider.is_enabled}
										/>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<CredentialExpiryBadge
											token={provider.token}
										/>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<span className="font-mono tabular-nums">
											{provider.user_count}
										</span>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<span className="font-mono tabular-nums">
											{mappedGroupsLabel(provider)}
										</span>
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</PageScrollArea>

			<NewIdentityProviderDialog
				open={isCreateOpen}
				onOpenChange={setIsCreateOpen}
				onCreated={onCreated}
			/>
		</PageWorkspace>
	);
}
