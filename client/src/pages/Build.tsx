/**
 * App-first home for the private Solution builder.
 *
 * A build is still stored as a private Solution, but users should not need to
 * understand that packaging model before they can describe an app. This page
 * creates the private project and its first session, then hands the prompt to
 * the existing owner-scoped builder workspace.
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
	ArrowRight,
	Bot,
	Check,
	Clock3,
	Loader2,
	Lock,
	Settings2,
	Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
	builderSolutionsQueryKey,
	useBuilderAccess,
} from "@/hooks/useBuilderAccess";
import {
	createBuilderSession,
	createBuilderSolution,
	deleteBuilderSolution,
	type BuilderSolution,
} from "@/services/builder";
import { slugify } from "@/components/builder/NewWithAIButton";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

interface NewBuild {
	solution: BuilderSolution;
	sessionId: string;
}

type BuildStage = "workspace" | "agent" | "opening";

export function Build() {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { isPlatformAdmin } = useAuth();
	const {
		aiConfigured,
		canBuild,
		hasPermission,
		isLoading,
		solutions,
	} = useBuilderAccess();
	const [name, setName] = useState("");
	const [prompt, setPrompt] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [buildStage, setBuildStage] = useState<BuildStage | null>(null);
	const [pendingLaunch, setPendingLaunch] = useState<NewBuild | null>(null);

	const orderedSolutions = useMemo(
		() =>
			[...solutions].sort((left, right) =>
				right.updated_at.localeCompare(left.updated_at),
			),
		[solutions],
	);

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
		onSuccess: ({ solution, sessionId }) => {
			setPendingLaunch({ solution, sessionId });
		},
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
				viewTransition: true,
				state: {
					initialPrompt: prompt.trim(),
					initialSessionId: pendingLaunch.sessionId,
				},
			});
			void queryClient.invalidateQueries({
				queryKey: builderSolutionsQueryKey,
			});
		});
		return () => window.cancelAnimationFrame(animationFrame);
	}, [buildStage, navigate, pendingLaunch, prompt, queryClient]);

	const canSubmit =
		Boolean(slugify(name)) &&
		Boolean(prompt.trim()) &&
		!createMutation.isPending;

	if (isLoading) {
		return (
			<div className="mx-auto max-w-6xl space-y-8">
				<Skeleton className="h-64 w-full rounded-3xl" />
				<Skeleton className="h-48 w-full rounded-2xl" />
			</div>
		);
	}

	if (!canBuild) {
		return (
			<div className="flex h-full items-center justify-center p-8 text-center">
				<div className="max-w-md space-y-2">
					<h1 className="text-xl font-semibold">Build is unavailable</h1>
					<p className="text-sm text-muted-foreground">
						{hasPermission
							? "AI has not been configured for this environment."
							: "Your account cannot use the app builder in this environment."}
					</p>
				</div>
			</div>
		);
	}

	if (isPlatformAdmin && !aiConfigured) {
		return (
			<div className="flex h-full items-center justify-center p-6">
				<Card className="w-full max-w-xl overflow-hidden border-primary/15 shadow-sm">
					<CardContent className="relative px-6 py-12 text-center sm:px-12">
						<div className="absolute inset-x-16 top-0 h-24 rounded-full bg-primary/10 blur-3xl" />
						<div className="relative">
							<div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/15">
								<Bot className="h-7 w-7 text-primary" />
							</div>
							<h1 className="mt-6 text-2xl font-semibold tracking-tight">
								Connect AI to start building
							</h1>
							<p className="mx-auto mt-3 max-w-md text-sm leading-6 text-muted-foreground">
								Build uses your platform AI provider to turn a prompt into a
								private, editable app. Configure a provider and model once,
								then this workspace is ready for everyone with builder access.
							</p>
							<Button
								className="mt-7"
								onClick={() =>
									navigate("/settings/ai", { viewTransition: true })
								}
							>
								<Settings2 className="mr-2 h-4 w-4" />
								Configure AI
							</Button>
							<p className="mt-4 text-xs text-muted-foreground">
								Only platform administrators can change this setting.
							</p>
						</div>
					</CardContent>
				</Card>
			</div>
		);
	}

	return (
		<div className="mx-auto h-full max-w-6xl space-y-10 overflow-auto pb-10">
			<section className="relative overflow-hidden rounded-3xl border bg-gradient-to-br from-primary/10 via-background to-background px-6 py-10 sm:px-10">
				<div className="relative mx-auto max-w-3xl text-center">
					<Badge variant="secondary" className="mb-4 gap-1.5">
						<Sparkles className="h-3.5 w-3.5" />
						AI app builder
					</Badge>
					<h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-5xl">
						What do you want to build?
					</h1>
					<p className="mx-auto mt-3 max-w-2xl text-pretty text-muted-foreground sm:text-lg">
						Describe the app in plain language. Your source, preview, and
						complete revision history stay together in a private workspace.
					</p>

					<div className="mt-8 min-h-56 rounded-2xl border bg-background/95 p-3 text-left shadow-sm transition-all duration-300 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:zoom-in-95">
						{buildStage ? (
							<BuildLaunchProgress stage={buildStage} appName={name.trim()} />
						) : (
							<>
								<Input
									value={name}
									aria-label="App name"
									placeholder="App name, for example Expense tracker"
									className="border-0 bg-transparent text-base shadow-none focus-visible:ring-0"
									onChange={(event) => {
										setName(event.target.value);
										setError(null);
									}}
								/>
								<Textarea
									value={prompt}
									aria-label="Describe your app"
									placeholder="Build an expense tracker with monthly totals, category filters, and receipt uploads…"
									className="min-h-32 resize-none border-0 bg-transparent text-base shadow-none focus-visible:ring-0"
									onChange={(event) => {
										setPrompt(event.target.value);
										setError(null);
									}}
								/>
								<div className="flex flex-wrap items-center justify-between gap-3 border-t px-1 pt-3">
									<p className="flex items-center gap-1.5 text-xs text-muted-foreground">
										<Lock className="h-3.5 w-3.5" />
										Private to you until you request promotion
									</p>
									<Button
										disabled={!canSubmit}
										onClick={() => createMutation.mutate()}
									>
										<Sparkles className="mr-2 h-4 w-4" />
										Start building
									</Button>
								</div>
							</>
						)}
					</div>

					{error ? (
						<p className="mt-3 text-sm text-destructive" role="alert">
							{error}
						</p>
					) : null}
				</div>
			</section>

			<section aria-labelledby="your-builds-heading">
				<div className="mb-4 flex items-end justify-between gap-4">
					<div>
						<h2 id="your-builds-heading" className="text-2xl font-semibold">
							Your builds
						</h2>
						<p className="mt-1 text-sm text-muted-foreground">
							Continue an app with its full conversation and revision history.
						</p>
					</div>
					<Badge variant="outline">{orderedSolutions.length}</Badge>
				</div>

				{orderedSolutions.length === 0 ? (
					<Card>
						<CardContent className="py-10 text-center">
							<Sparkles className="mx-auto h-8 w-8 text-muted-foreground" />
							<p className="mt-3 font-medium">No apps in progress</p>
							<p className="mt-1 text-sm text-muted-foreground">
								Describe your first app above to create a private workspace.
							</p>
						</CardContent>
					</Card>
				) : (
					<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
						{orderedSolutions.map((solution) => (
							<Card key={solution.id} className="group">
								<CardHeader className="pb-3">
									<div className="flex items-start justify-between gap-3">
										<div className="min-w-0">
											<CardTitle className="truncate text-lg">
												{solution.name}
											</CardTitle>
											<p className="mt-1 truncate text-xs text-muted-foreground">
												{solution.slug}
											</p>
										</div>
										<Badge variant="outline" className="shrink-0 gap-1">
											<Lock className="h-3 w-3" />
											Private
										</Badge>
									</div>
								</CardHeader>
								<CardContent>
									<div className="flex items-center justify-between gap-3">
										<p className="flex items-center gap-1.5 text-xs text-muted-foreground">
											<Clock3 className="h-3.5 w-3.5" />
											Updated{" "}
											{new Date(solution.updated_at).toLocaleDateString()}
										</p>
										<Button
											variant="ghost"
											size="sm"
											onClick={() =>
												navigate(
													`/solutions/${solution.id}/builder`,
												)
											}
										>
											Open builder
											<ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-0.5" />
										</Button>
									</div>
								</CardContent>
							</Card>
						))}
					</div>
				)}
			</section>
		</div>
	);
}

const BUILD_STEPS: Array<{
	id: BuildStage;
	label: string;
	detail: string;
}> = [
	{
		id: "workspace",
		label: "Creating your private workspace",
		detail: "Setting up source and revision history",
	},
	{
		id: "agent",
		label: "Starting the builder",
		detail: "Preparing your first conversation",
	},
	{
		id: "opening",
		label: "Opening the workbench",
		detail: "Carrying your prompt into the builder",
	},
];

function BuildLaunchProgress({
	stage,
	appName,
}: {
	stage: BuildStage;
	appName: string;
}) {
	const activeIndex = BUILD_STEPS.findIndex((step) => step.id === stage);
	return (
		<div
			className="flex min-h-52 items-center px-3 py-4 sm:px-8"
			aria-live="polite"
			role="status"
			aria-label={`Starting ${appName}`}
		>
			<div className="w-full space-y-5">
				<div className="h-1.5 overflow-hidden rounded-full bg-muted/60">
					<div
						className={cn(
							"h-full rounded-full bg-gradient-to-r from-primary via-primary/70 to-primary/40 transition-all duration-500 motion-safe:animate-pulse",
							activeIndex === 0
								? "w-1/3"
								: activeIndex === 1
									? "w-2/3"
									: "w-full",
						)}
					/>
				</div>
				<div>
					<p className="text-sm font-medium">
						Starting {appName || "your app"}
					</p>
					<p className="mt-1 text-xs text-muted-foreground">
						Your app will open as soon as its workspace is ready.
					</p>
				</div>
				<div className="space-y-3">
					{BUILD_STEPS.map((step, index) => {
						const complete = index < activeIndex;
						const active = index === activeIndex;
						return (
							<div key={step.id} className="flex items-center gap-3">
								<div
									className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors duration-300 ${
										complete
											? "border-primary bg-primary text-primary-foreground"
											: active
												? "border-primary/40 bg-primary/10 text-primary"
												: "border-border text-muted-foreground"
									}`}
								>
									{complete ? (
										<Check className="h-4 w-4" />
									) : active ? (
										<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									) : (
										<span className="text-xs">{index + 1}</span>
									)}
								</div>
								<div
									className={cn(
										"transition-opacity duration-300",
										active || complete ? "opacity-100" : "opacity-55",
									)}
								>
									<p className="text-sm font-medium">{step.label}</p>
									<p className="text-xs text-muted-foreground">
										{step.detail}
									</p>
								</div>
							</div>
						);
					})}
				</div>
			</div>
		</div>
	);
}
