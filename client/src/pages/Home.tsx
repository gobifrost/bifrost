import { useCollectionOrder } from "@/hooks/useCollectionOrder";
import { isVisibleCollection } from "@/services/home";
import { useState, useRef, useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Search } from "lucide-react";
import { $api } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import { useAuth } from "@/contexts/AuthContext";
import { useCreateConversation } from "@/hooks/useChat";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { CatalogFilters } from "./Home/components/CatalogFilters";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { WorkspaceTabs } from "@/components/layout/WorkspaceTabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { PageLoader } from "@/components/PageLoader";
import { toast } from "sonner";
import type {
	HomeCollection,
	HomeCollectionWrite,
	HomeResource,
} from "@/services/home";
import { HomeBrowse } from "./Home/components/HomeBrowse";
import { CollectionNavigation } from "./Home/components/CollectionNavigation";
import { CollectionEditor } from "./Home/components/CollectionEditor";
import "./Home/home-search.css";

const NO_RESOURCES: HomeResource[] = [];

/** Home launches work using existing resource grants. Collections organize; they never authorize. */
export function Home() {
	const { isPlatformAdmin } = useAuth();
	const navigate = useNavigate();
	const [params, setParams] = useSearchParams();
	const queryClient = useQueryClient();
	const home = $api.useQuery("get", "/api/home");
	const createConversation = useCreateConversation();
	const [editor, setEditor] = useState<HomeCollection | "new" | null>(null);
	const [deleteRequested, setDeleteRequested] = useState(false);
	const [editorError, setEditorError] = useState("");
	const [opening, setOpening] = useState(false);
	const refresh = () =>
		queryClient.invalidateQueries({ queryKey: ["get", "/api/home"] });
	const preference = $api.useMutation(
		"put",
		"/api/home/preferences/{resource_key}",
		{ onSuccess: refresh },
	);
	const create = $api.useMutation("post", "/api/home/collections");
	const update = $api.useMutation(
		"put",
		"/api/home/collections/{collection_id}",
	);
	const remove = $api.useMutation(
		"delete",
		"/api/home/collections/{collection_id}",
	);
	const busy = create.isPending || update.isPending || remove.isPending;
	const resources = home.data?.resources ?? NO_RESOURCES;
	const { collections, reorder } = useCollectionOrder(
		(home.data?.collections ?? []).filter(isVisibleCollection),
	);
	const selected = collections.find(
		(collection) => collection.id === params.get("collection"),
	);
	const search = params.get("q") ?? "";
	const kind = params.get("type") ?? "all";
	const org = params.get("org") ?? "all";
	const grid = params.get("view") !== "list";
	const collectionParam = params.get("collection");
	const pageParam = params.get("page");
	const scrollRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		scrollRef.current?.scrollTo?.({ top: 0 });
	}, [search, kind, org, collectionParam, pageParam]);
	const newCollection = params.get("newCollection") === "1";
	const activeEditor = newCollection ? "new" : editor;
	const closeEditor = () => {
		setEditor(null);
		setEditorError("");
		if (newCollection) updateParam("newCollection", null);
	};
	const updateParam = (key: string, value: string | null) =>
		setParams(
			(previous) => {
				const next = new URLSearchParams(previous);
				if (value) next.set(key, value);
				else next.delete(key);
				next.delete("page");
				if (key === "collection") {
					next.delete("newCollection");
					if (next.get("sort") === "collection") next.delete("sort");
				}
				if (key === "org") next.delete("collection");
				if (key === "type" && value === "all") next.delete("catalog");
				return next;
			},
			{ replace: true },
		);
	const inScope = (resource: HomeResource) =>
		org === "all" ||
		(org === "global"
			? !resource.organization_id
			: resource.organization_id === org || !resource.organization_id);
	const ordered = selected
		? (selected.resource_keys ?? []).flatMap(
				(key) =>
					resources.find((resource) => resource.key === key) ?? [],
			)
		: resources;
	const filtered = ordered.filter(
		(resource) =>
			inScope(resource) &&
			(kind === "all" || resource.kind === kind) &&
			`${resource.name} ${resource.description ?? ""} ${resource.organization_name}`
				.toLowerCase()
				.includes(search.toLowerCase()),
	);
	const sort = params.get("sort") ?? "recommended";
	if (sort === "recent")
		filtered.sort(
			(a, b) =>
				(b.last_opened_at ? Date.parse(b.last_opened_at) : 0) -
					(a.last_opened_at ? Date.parse(a.last_opened_at) : 0) ||
				a.name.localeCompare(b.name),
		);
	else if (sort === "name")
		filtered.sort((a, b) => a.name.localeCompare(b.name));

	if (sort === "recommended") {
		const query = search.trim().toLowerCase();
		const relevance = (r: HomeResource) =>
			!query
				? 0
				: r.name.toLowerCase() === query
					? 3
					: r.name.toLowerCase().startsWith(query)
						? 2
						: r.name.toLowerCase().includes(query)
							? 1
							: 0;
		filtered.sort(
			(a, b) =>
				relevance(b) - relevance(a) ||
				Number(b.pinned) - Number(a.pinned) ||
				(selected ? 0 : a.name.localeCompare(b.name)),
		);
	}

	const requestedPage = Math.max(0, Number(params.get("page")) || 0);
	const page = Math.min(
		Math.floor(requestedPage),
		Math.max(0, Math.ceil(filtered.length / 12) - 1),
	);
	const visible = filtered.slice(page * 12, (page + 1) * 12);
	const openResource = async (resource: HomeResource) => {
		if (opening) return;
		setOpening(true);
		try {
			let href = resource.href;
			if (resource.kind === "agent") {
				const conversation = await createConversation.mutateAsync({
					body: { agent_id: resource.id, channel: "chat" },
				});
				href = `/chat/${conversation.id}`;
			}
			try {
				await preference.mutateAsync({
					params: { path: { resource_key: resource.key } },
					body: { opened: true },
				});
			} catch {
				toast.error("Could not save this item to recent work");
			}
			navigate(href);
		} catch {
			/* Conversation mutation reports the actionable API error. */
		} finally {
			setOpening(false);
		}
	};
	const pinResource = async (resource: HomeResource) => {
		try {
			await preference.mutateAsync({
				params: { path: { resource_key: resource.key } },
				body: { pinned: !resource.pinned, opened: false },
			});
		} catch (error) {
			toast.error(getErrorMessage(error, "Could not save favorite"));
		}
	};
	const openEditor = (collection: HomeCollection | "new") => {
		setEditorError("");
		setDeleteRequested(false);
		setEditor(collection);
	};
	const saveCollection = async (data: HomeCollectionWrite) => {
		setEditorError("");
		try {
			const result =
				activeEditor && activeEditor !== "new"
					? await update.mutateAsync({
							params: {
								path: { collection_id: activeEditor.id },
							},
							body: data,
						})
					: await create.mutateAsync({ body: data });
			await refresh();
			setEditor(null);
			updateParam("collection", result.id);
			toast.success("Collection saved");
		} catch (error) {
			setEditorError(getErrorMessage(error, "Could not save collection"));
		}
	};
	const deleteCollection = async () => {
		if (!editor || editor === "new") return;
		try {
			await remove.mutateAsync({
				params: { path: { collection_id: editor.id } },
			});
			await refresh();
			setEditor(null);
			if (selected?.id === editor.id) updateParam("collection", null);
		} catch (error) {
			setEditorError(
				getErrorMessage(error, "Could not delete collection"),
			);
		}
	};
	if (home.isLoading) return <PageLoader message="Loading your workspace…" />;
	if (home.isError)
		return (
			<div className="space-y-4">
				<h1 className="font-display text-2xl">Home could not load</h1>
				<p role="alert" className="text-muted-foreground">
					Your resources and collections could not be retrieved.
				</p>
				<Button
					onClick={() => void home.refetch()}
					disabled={home.isFetching}
				>
					Try again
				</Button>
			</div>
		);
	return (
		<PageWorkspace className="mx-auto w-full max-w-[1400px] gap-5">
			<div className="shrink-0 space-y-4">
				<ListPageHeader
					title="Workspace"
					titleSlot={<WorkspaceTabs />}
					className="flex-row"
					actionsClassName="flex-nowrap"
					description="Apps, forms and agents, together."
					actions={
						<Button asChild>
							<Link to="/chat" aria-label="New chat">
								<MessageSquare className="size-4" />
								<span className="hidden sm:inline">New chat</span>
							</Link>
						</Button>
					}
				/>
				<div className="flex flex-col gap-3">
					<div className="home-search relative min-w-0 flex-1">
						<Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
						<Input
							className="h-12 pl-10 sm:h-14"
							aria-label="Search Home resources"
							placeholder="Find apps, forms, and agents…"
							value={search}
							onChange={(event) =>
								updateParam("q", event.target.value)
							}
						/>
					</div>
				</div>
				<CollectionNavigation
					collections={collections.filter(
						(collection) =>
							org === "all" ||
							!collection.shared ||
							!collection.organization_id ||
							collection.organization_id === org,
					)}
					selected={selected?.id ?? null}
					onSelect={(id) => updateParam("collection", id)}
					onCreate={() => openEditor("new")}
					onEdit={openEditor}
					onDelete={(collection) => {
						openEditor(collection);
						setDeleteRequested(true);
					}}
					onReorder={reorder}
				/>
			</div>
			<PageScrollArea
				ref={scrollRef}
				aria-label="Home workspace"
				className="space-y-7 pb-2 pr-1"
			>
				<HomeBrowse
					filters={
						<>
							{" "}
							<CatalogFilters
								value={kind}
								resources={ordered.filter(inScope)}
								onChange={(value) => updateParam("type", value)}
							/>{" "}
							{isPlatformAdmin && (
								<OrganizationSelect
									aria-label="Organization filter"
									value={
										org === "all"
											? undefined
											: org === "global"
												? null
												: org
									}
									onChange={(value) =>
										updateParam(
											"org",
											value === undefined
												? "all"
												: value === null
													? "global"
													: value,
										)
									}
									showAll
									showGlobal
									triggerClassName="w-44 sm:w-52"
								/>
							)}
						</>
					}
					selected={selected}
					onEdit={openEditor}
					total={filtered.length}
					grid={grid}
					sort={sort}
					visible={visible}
					resourceCount={resources.length}
					busy={opening || preference.isPending}
					page={page}
					updateParam={updateParam}
					onOpen={openResource}
					onPin={pinResource}
					onPageChange={(offset) =>
						setParams(
							(previous) => {
								const next = new URLSearchParams(previous);
								next.set("page", String(offset / 12));
								return next;
							},
							{ replace: true },
						)
					}
				/>
			</PageScrollArea>
			{activeEditor && (
				<CollectionEditor
					key={activeEditor === "new" ? "new" : activeEditor.id}
					collection={
						activeEditor === "new" ? undefined : activeEditor
					}
					resources={resources}
					initialConfirmDelete={deleteRequested}
					isAdmin={isPlatformAdmin}
					onClose={closeEditor}
					onSave={saveCollection}
					onDelete={deleteCollection}
					busy={busy}
					error={editorError}
				/>
			)}
		</PageWorkspace>
	);
}
