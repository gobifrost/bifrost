/** Prompt-first home and support catalog for native Solution builds. */

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	ArrowRight,
	AlertTriangle,
	Building2,
	Check,
	Clock3,
	Database,
	Filter,
	FolderKanban,
	Loader2,
	Lock,
	Search,
	Settings2,
	ShieldCheck,
	Sparkles,
	Users,
} from "lucide-react";

import { slugify } from "@/components/builder/NewWithAIButton";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/contexts/AuthContext";
import {
	builderSolutionsQueryKey,
	useBuilderAccess,
} from "@/hooks/useBuilderAccess";
import { useUsersFiltered } from "@/hooks/useUsers";
import { cn } from "@/lib/utils";
import {
	createBuilderSession,
	createBuilderSolution,
	deleteBuilderSolution,
	ensureGlobalWorkspace,
	getGlobalWorkspace,
	listBuilderSolutions,
	type BuilderSolution,
} from "@/services/builder";

interface NewBuild {
	solution: BuilderSolution;
	sessionId: string;
}

type BuildStage = "workspace" | "agent" | "opening";
type CatalogView = "mine" | "all";
const SUPPORT_PAGE_SIZE = 50;

export function Build() {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { user } = useAuth();
	const {
		builderReady,
		blockers,
		canBuild,
		canViewAll,
		hasPermission,
		isLoading,
		isPlatformAdmin,
		solutions,
	} = useBuilderAccess();
	const [name, setName] = useState("");
	const [prompt, setPrompt] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [buildStage, setBuildStage] = useState<BuildStage | null>(null);
	const [pendingLaunch, setPendingLaunch] = useState<NewBuild | null>(null);
	const [catalogView, setCatalogView] = useState<CatalogView>("mine");
	const [search, setSearch] = useState("");
	const [organizationId, setOrganizationId] = useState<string | undefined>();
	const [ownerUserId, setOwnerUserId] = useState("");
	const [supportPage, setSupportPage] = useState(0);
	const [globalError, setGlobalError] = useState<string | null>(null);

	const globalWorkspaceQuery = useQuery({
		queryKey: ["builder", "global-workspace"],
		queryFn: ({ signal }) => getGlobalWorkspace({ signal }),
		enabled: isPlatformAdmin && builderReady,
	});
	const openGlobalWorkspaceMutation = useMutation({
		mutationFn: async () => {
			setGlobalError(null);
			return globalWorkspaceQuery.data?.exists
				? globalWorkspaceQuery.data
				: ensureGlobalWorkspace();
		},
		onSuccess: (workspace) => {
			void queryClient.invalidateQueries({
				queryKey: ["builder", "global-workspace"],
			});
			if (workspace.solution_id) {
				navigate(`/solutions/${workspace.solution_id}/builder`);
			}
		},
		onError: (caught: Error) => setGlobalError(caught.message),
	});

	const allSolutionsQuery = useQuery({
		queryKey: [
			...builderSolutionsQueryKey,
			"all",
			organizationId ?? null,
			ownerUserId || null,
			search.trim(),
			supportPage,
		],
		queryFn: ({ signal }) =>
			listBuilderSolutions({
				view: "all",
				organizationId,
				ownerUserId: ownerUserId || null,
				search,
				limit: SUPPORT_PAGE_SIZE,
				offset: supportPage * SUPPORT_PAGE_SIZE,
				signal,
			}),
		enabled: catalogView === "all" && canViewAll,
		placeholderData: (previous) => previous,
	});
	const visibleSolutions = useMemo(() => {
		const source =
			catalogView === "all"
				? (allSolutionsQuery.data?.solutions ?? [])
				: solutions.filter((solution) => {
						const needle = search.trim().toLowerCase();
						if (!needle) return true;
						return [
							solution.name,
							solution.slug,
							solution.owner_name,
							solution.owner_email,
							solution.organization_name,
						]
							.filter(Boolean)
							.some((value) => value!.toLowerCase().includes(needle));
				  });
		return [...source].sort((left, right) =>
			right.updated_at.localeCompare(left.updated_at),
		);
	}, [allSolutionsQuery.data?.solutions, catalogView, search, solutions]);

	const createMutation = useMutation({
		mutationFn: async (): Promise<NewBuild> => {
			setBuildStage("workspace");
			const solution = await createBuilderSolution({
				name: name.trim(),
				slug: slugify(name),
			});
			try {
				setBuildStage("agent");
				const session = await createBuilderSession(solution.id);
				setBuildStage("opening");
				return { solution, sessionId: session.id };
			} catch (caught) {
				await deleteBuilderSolution(solution.id);
				throw caught;
			}
		},
		onSuccess: (created) => setPendingLaunch(created),
		onError: (caught: Error) => {
			setBuildStage(null);
			setPendingLaunch(null);
			setError(caught.message);
		},
	});

	useEffect(() => {
		if (!pendingLaunch || buildStage !== "opening") return;
		const animationFrame = window.requestAnimationFrame(() => {
			navigate(`/solutions/${pendingLaunch.solution.id}/builder`, {
				state: {
					initialPrompt: prompt.trim(),
					initialSessionId: pendingLaunch.sessionId,
				},
			});
			void queryClient.invalidateQueries({ queryKey: builderSolutionsQueryKey });
		});
		return () => window.cancelAnimationFrame(animationFrame);
	}, [buildStage, navigate, pendingLaunch, prompt, queryClient]);

	const canSubmit =
		builderReady &&
		Boolean(slugify(name)) &&
		Boolean(prompt.trim()) &&
		!createMutation.isPending;

	if (isLoading) {
		return (
			<div className="mx-auto max-w-7xl space-y-5 p-1">
				<Skeleton className="h-10 w-48" />
				<Skeleton className="h-72 w-full rounded-3xl" />
				<Skeleton className="h-64 w-full rounded-3xl" />
			</div>
		);
	}

	if (!canBuild) {
		return (
			<div className="flex h-full items-center justify-center p-8 text-center">
				<div className="max-w-md space-y-2">
					<h1 className="text-xl font-semibold">Build is unavailable</h1>
					<p className="text-sm leading-6 text-muted-foreground">
						{hasPermission
							? "Builder has not been enabled for this environment yet."
							: "Your account does not have permission to build apps in this environment."}
					</p>
				</div>
			</div>
		);
	}

	if (isPlatformAdmin && !builderReady) {
		return (
			<div className="mx-auto flex h-full max-w-5xl items-center justify-center p-4 sm:p-8">
				<section className="grid w-full overflow-hidden rounded-3xl border bg-card shadow-sm lg:grid-cols-[1.2fr_.8fr]">
					<div className="p-6 sm:p-10">
						<Badge variant="outline" className="gap-1.5">
							<Settings2 className="h-3.5 w-3.5" /> Administrator setup
						</Badge>
						<h1 className="mt-5 text-3xl font-semibold tracking-tight">
							Finish connecting Builder
						</h1>
						<p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
							Build stays hidden from ordinary users until AI, an isolated runner, and live connectivity are verified. The setup screen walks through each requirement and enables access only when it is safe to proceed.
						</p>
						<Button className="mt-7" onClick={() => navigate("/settings/builder", { viewTransition: true })}>
							<Settings2 className="h-4 w-4" /> Open Builder setup
						</Button>
					</div>
					<div className="border-t bg-muted/30 p-6 lg:border-l lg:border-t-0 sm:p-8">
						<p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Still needed</p>
						<div className="mt-4 space-y-3">
							{blockers.length > 0 ? blockers.map((blocker) => (
								<div key={blocker.code} className="flex gap-3 rounded-2xl border bg-background p-3">
									<span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-600">{blocker.code === "ai_not_configured" ? <Sparkles className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}</span>
									<div><p className="text-sm font-medium">{blocker.message}</p><p className="mt-0.5 text-xs leading-5 text-muted-foreground">{blocker.action}</p></div>
								</div>
							)) : <p className="text-sm text-muted-foreground">Open setup to verify the final connection.</p>}
						</div>
					</div>
				</section>
			</div>
		);
	}

	return (
		<div className="mx-auto h-full max-w-7xl space-y-6 overflow-auto pb-10">
			<header className="flex flex-wrap items-end justify-between gap-3">
				<div>
					<div className="flex items-center gap-2">
						<h1 className="text-3xl font-semibold tracking-tight">Build</h1>
						<Badge variant="secondary" className="gap-1.5 text-emerald-700 dark:text-emerald-300"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Ready</Badge>
					</div>
					<p className="mt-1 text-sm text-muted-foreground">Create complete Bifrost apps and continue every conversation from where you left it.</p>
				</div>
				{isPlatformAdmin ? <Button variant="ghost" size="sm" onClick={() => navigate("/settings/builder")}><Settings2 className="h-4 w-4" />Builder setup</Button> : null}
			</header>

			{isPlatformAdmin ? (
				<section className="relative overflow-hidden rounded-3xl border border-primary/20 bg-gradient-to-br from-primary/[0.07] via-card to-card p-5 sm:p-6">
					<div className="absolute -right-16 -top-20 h-48 w-48 rounded-full bg-primary/10 blur-3xl" aria-hidden="true" />
					<div className="relative flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
						<div className="flex min-w-0 gap-4">
							<span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
								<Database className="h-5 w-5" />
							</span>
							<div className="min-w-0">
								<div className="flex flex-wrap items-center gap-2">
									<p className="text-xs font-semibold uppercase tracking-wider text-primary">Administrator workspace</p>
									{globalWorkspaceQuery.data?.exists ? (
										<Badge variant="outline" className="bg-background/70">
											{globalWorkspaceQuery.data.has_pending_proposal ? "Proposal ready" : "Synced with live _repo"}
										</Badge>
									) : null}
								</div>
								<h2 className="mt-1 text-xl font-semibold tracking-tight">Global Workspace</h2>
								<p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
									Ask AI to propose changes to the instance-wide <code className="rounded bg-muted px-1 py-0.5 text-xs text-foreground">_repo</code>. Manifests stay locked, and nothing changes live until an administrator validates and applies the reviewed diff.
								</p>
								{globalError || globalWorkspaceQuery.isError ? (
									<p className="mt-2 text-sm text-destructive" role="alert">
										{globalError ?? (globalWorkspaceQuery.error as Error).message}
									</p>
								) : null}
							</div>
						</div>
						<Button
							className="shrink-0"
							disabled={globalWorkspaceQuery.isLoading || openGlobalWorkspaceMutation.isPending}
							onClick={() => openGlobalWorkspaceMutation.mutate()}
						>
							{globalWorkspaceQuery.isLoading || openGlobalWorkspaceMutation.isPending ? (
								<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Database className="h-4 w-4" />
							)}
							{globalWorkspaceQuery.data?.exists ? "Open Global Workspace" : "Create Global Workspace"}
						</Button>
					</div>
				</section>
			) : null}

			<section className="grid overflow-hidden rounded-3xl border bg-card lg:grid-cols-[minmax(0,1.55fr)_minmax(260px,.45fr)]">
				<div className="p-4 sm:p-6">
					<div className="mb-4 flex items-start justify-between gap-4">
						<div><p className="text-xs font-semibold uppercase tracking-wider text-primary">New app</p><h2 className="mt-1 text-2xl font-semibold tracking-tight">What should Bifrost build?</h2></div>
						<Sparkles className="h-5 w-5 text-primary" />
					</div>
					<div className="min-h-56 rounded-2xl border bg-background p-3 shadow-sm">
						{buildStage ? <BuildLaunchProgress stage={buildStage} appName={name.trim()} /> : (
							<>
								<Input value={name} aria-label="App name" placeholder="Name your app" className="h-11 border-0 bg-transparent px-2 text-base font-medium shadow-none focus-visible:ring-0" onChange={(event) => { setName(event.target.value); setError(null); }} />
								<Textarea value={prompt} aria-label="Describe your app" placeholder="Describe who will use it, what they need to accomplish, and the data or systems it should connect to…" className="min-h-32 resize-none border-0 bg-transparent px-2 text-base leading-6 shadow-none focus-visible:ring-0" onChange={(event) => { setPrompt(event.target.value); setError(null); }} />
								<div className="flex flex-wrap items-center justify-between gap-3 border-t px-2 pt-3">
									<p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Lock className="h-3.5 w-3.5" />Private workspace — invite collaborators when ready</p>
									<Button disabled={!canSubmit} onClick={() => createMutation.mutate()}><Sparkles className="h-4 w-4" />Start building</Button>
								</div>
							</>
						)}
					</div>
					{error ? <p className="mt-3 text-sm text-destructive" role="alert">{error}</p> : null}
				</div>
				<aside className="border-t bg-muted/25 p-5 lg:border-l lg:border-t-0 sm:p-6">
					<p className="text-sm font-semibold">A useful first brief includes</p>
					<div className="mt-4 space-y-4 text-sm">
						<PromptHint number="1" title="The outcome" detail="What should someone be able to finish?" />
						<PromptHint number="2" title="The audience" detail="Who uses it, and what access do they need?" />
						<PromptHint number="3" title="The systems" detail="Mention tables, files, agents, or integrations." />
					</div>
					<div className="mt-6 border-t pt-4"><p className="text-xs leading-5 text-muted-foreground">The Builder Agent uses the <strong className="font-medium text-foreground">bifrost-build</strong> Skill and the same solution-aware MCP tools available to external coding harnesses.</p></div>
				</aside>
			</section>

			<section aria-labelledby="build-library-heading" className="overflow-hidden rounded-3xl border bg-card">
				<div className="flex flex-wrap items-center justify-between gap-3 border-b p-4 sm:px-5">
					<div><h2 id="build-library-heading" className="text-lg font-semibold">Build library</h2><p className="text-xs text-muted-foreground">Your work stays focused; support-wide discovery is deliberate.</p></div>
					{canViewAll ? <Tabs value={catalogView} onValueChange={(value) => { setCatalogView(value as CatalogView); setOwnerUserId(""); setOrganizationId(undefined); setSupportPage(0); }}><TabsList><TabsTrigger value="mine"><FolderKanban className="h-3.5 w-3.5" />My work</TabsTrigger><TabsTrigger value="all"><Building2 className="h-3.5 w-3.5" />All customer work</TabsTrigger></TabsList></Tabs> : <Badge variant="outline">{visibleSolutions.length}</Badge>}
				</div>
				<div className="flex flex-col gap-3 border-b bg-muted/15 p-3 sm:flex-row sm:items-center sm:px-5">
					<div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input aria-label="Search builds" value={search} onChange={(event) => { setSearch(event.target.value); setSupportPage(0); }} placeholder={catalogView === "all" ? "Search app, customer, or owner" : "Search your apps"} className="pl-9" /></div>
					{catalogView === "all" ? (
						<SupportFilters
							organizationId={organizationId}
							ownerUserId={ownerUserId}
							onOrganizationChange={(value) => { setOrganizationId(value); setOwnerUserId(""); setSupportPage(0); }}
							onOwnerChange={(value) => { setOwnerUserId(value); setSupportPage(0); }}
						/>
					) : null}
				</div>

				{catalogView === "all" && allSolutionsQuery.isError ? (
					<div className="flex flex-col items-center gap-3 px-6 py-12 text-center" role="alert">
						<AlertTriangle className="h-8 w-8 text-destructive" />
						<div>
							<p className="font-medium">Could not load customer work</p>
							<p className="mt-1 text-sm text-muted-foreground">{allSolutionsQuery.error.message}</p>
						</div>
						<Button variant="outline" size="sm" onClick={() => void allSolutionsQuery.refetch()}>Try again</Button>
					</div>
				) : allSolutionsQuery.isFetching && catalogView === "all" && !allSolutionsQuery.data ? <div className="space-y-px bg-border"><Skeleton className="h-20 rounded-none" /><Skeleton className="h-20 rounded-none" /></div> : visibleSolutions.length === 0 ? (
					<div className="px-6 py-12 text-center"><Sparkles className="mx-auto h-8 w-8 text-muted-foreground" /><p className="mt-3 font-medium">{search || organizationId || ownerUserId ? "No matching builds" : "No apps in progress"}</p><p className="mt-1 text-sm text-muted-foreground">{catalogView === "mine" ? "Describe an app above to create your first private workspace." : "Try another customer, owner, or search term."}</p></div>
				) : (
					<div className="divide-y">
						{visibleSolutions.map((solution) => <BuildRow key={solution.id} solution={solution} currentUserId={user?.id} onOpen={() => navigate(`/solutions/${solution.id}/builder`)} />)}
					</div>
				)}
				{catalogView === "all" && allSolutionsQuery.data && allSolutionsQuery.data.total > 0 ? (
					<div className="flex flex-col gap-2 border-t px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-5">
						<p className="text-muted-foreground">
							Showing {supportPage * SUPPORT_PAGE_SIZE + 1}–{Math.min((supportPage + 1) * SUPPORT_PAGE_SIZE, allSolutionsQuery.data.total)} of {allSolutionsQuery.data.total}
						</p>
						<div className="flex gap-2">
							<Button variant="outline" size="sm" disabled={supportPage === 0 || allSolutionsQuery.isFetching} onClick={() => setSupportPage((page) => Math.max(0, page - 1))}>Previous</Button>
							<Button variant="outline" size="sm" disabled={(supportPage + 1) * SUPPORT_PAGE_SIZE >= allSolutionsQuery.data.total || allSolutionsQuery.isFetching} onClick={() => setSupportPage((page) => page + 1)}>Next</Button>
						</div>
					</div>
				) : null}
			</section>
		</div>
	);
}

function SupportFilters({ organizationId, ownerUserId, onOrganizationChange, onOwnerChange }: { organizationId: string | undefined; ownerUserId: string; onOrganizationChange: (value: string | undefined) => void; onOwnerChange: (value: string) => void }) {
	const usersQuery = useUsersFiltered(organizationId);
	const ownerOptions = useMemo(
		() => (usersQuery.data ?? []).filter((candidate) => candidate.is_active).map((candidate) => ({
			value: candidate.id,
			label: candidate.name || candidate.email,
			description: candidate.name ? candidate.email : undefined,
		})),
		[usersQuery.data],
	);
	return (
		<>
			<div className="w-full sm:w-56"><OrganizationSelect value={organizationId} onChange={(value) => onOrganizationChange(value ?? undefined)} showGlobal={false} showAll placeholder="All organizations" triggerClassName="h-9 bg-background" /></div>
			<div className="w-full sm:w-56"><Combobox aria-label="Filter by owner" options={ownerOptions} value={ownerUserId} onValueChange={onOwnerChange} placeholder="All owners" searchPlaceholder="Find an owner…" isLoading={usersQuery.isLoading} className="h-9 bg-background" /></div>
			{organizationId || ownerUserId ? <Button variant="ghost" size="sm" onClick={() => { onOrganizationChange(undefined); onOwnerChange(""); }}><Filter className="h-4 w-4" />Clear</Button> : null}
		</>
	);
}

function PromptHint({ number, title, detail }: { number: string; title: string; detail: string }) {
	return <div className="flex gap-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border bg-background text-xs font-semibold">{number}</span><div><p className="font-medium leading-5">{title}</p><p className="text-xs leading-5 text-muted-foreground">{detail}</p></div></div>;
}

function BuildRow({ solution, currentUserId, onOpen }: { solution: BuilderSolution; currentUserId?: string; onOpen: () => void }) {
	const owned = solution.owner_user_id === currentUserId || solution.caller_access === "owner";
	return (
		<div className="group grid gap-3 px-4 py-4 transition-colors hover:bg-muted/20 sm:grid-cols-[minmax(0,1fr)_minmax(180px,.55fr)_auto] sm:items-center sm:px-5">
			<div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="truncate font-medium">{solution.name}</p><Badge variant="outline" className="h-5 gap-1 text-[10px]">{owned ? <Lock className="h-2.5 w-2.5" /> : <Users className="h-2.5 w-2.5" />}{owned ? "Owned" : solution.caller_access === "support" ? "Support access" : solution.collaborator_access === "view" ? "Shared · view" : "Shared · edit"}</Badge>{solution.promotion_status === "requested" ? <Badge variant="secondary" className="h-5 text-[10px]">In review</Badge> : null}</div><p className="mt-1 truncate text-xs text-muted-foreground">{solution.slug}</p></div>
			<div className="min-w-0 text-xs text-muted-foreground"><p className="truncate">{solution.organization_name ?? "No organization"}</p><p className="mt-1 truncate">{owned ? "You" : solution.owner_name || solution.owner_email || "Unknown owner"} · Updated {new Date(solution.updated_at).toLocaleDateString()}</p></div>
			<Button variant="ghost" size="sm" onClick={onOpen}>Open <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" /></Button>
		</div>
	);
}

const BUILD_STEPS: Array<{ id: BuildStage; label: string; detail: string }> = [
	{ id: "workspace", label: "Creating your private workspace", detail: "Setting up source and revision history" },
	{ id: "agent", label: "Starting the Builder Agent", detail: "Loading its Skill and solution-aware tools" },
	{ id: "opening", label: "Opening the workbench", detail: "Restoring your prompt and live preview" },
];

function BuildLaunchProgress({ stage, appName }: { stage: BuildStage; appName: string }) {
	const activeIndex = BUILD_STEPS.findIndex((step) => step.id === stage);
	return (
		<div className="flex min-h-52 items-center px-3 py-4 sm:px-6" aria-live="polite" role="status" aria-label={`Starting ${appName}`}>
			<div className="w-full space-y-5">
				<div className="h-1.5 overflow-hidden rounded-full bg-muted/60"><div className={cn("h-full rounded-full bg-primary transition-all duration-500 motion-safe:animate-pulse", activeIndex === 0 ? "w-1/3" : activeIndex === 1 ? "w-2/3" : "w-full")} /></div>
				<div><p className="text-sm font-medium">Starting {appName || "your app"}</p><p className="mt-1 text-xs text-muted-foreground">The workbench opens as soon as its private session is ready.</p></div>
				<div className="grid gap-3 sm:grid-cols-3">{BUILD_STEPS.map((step, index) => { const complete = index < activeIndex; const active = index === activeIndex; return <div key={step.id} className={cn("rounded-2xl border p-3 transition-colors", active && "border-primary/40 bg-primary/5", complete && "bg-muted/30")}><div className="flex items-center gap-2">{complete ? <Check className="h-4 w-4 text-primary" /> : active ? <Loader2 className="h-4 w-4 animate-spin text-primary motion-reduce:animate-none" /> : <Clock3 className="h-4 w-4 text-muted-foreground" />}<p className="text-xs font-medium">{step.label}</p></div><p className="mt-1 pl-6 text-[11px] leading-4 text-muted-foreground">{step.detail}</p></div>; })}</div>
			</div>
		</div>
	);
}
