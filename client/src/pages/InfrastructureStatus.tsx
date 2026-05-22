import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
	Activity,
	AlertTriangle,
	CheckCircle2,
	Clock,
	ExternalLink,
	GitBranch,
	Info,
	Network,
	ServerCog,
	ShieldAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

type GraphStatus =
	| "Healthy"
	| "Advisory"
	| "Degraded"
	| "Blocked"
	| "Unknown"
	| "Disabled";

type GraphImpact = "None" | "Limited" | "Broad" | "Instance-wide";

interface GraphNode {
	id: string;
	label: string;
	domain: string;
	status: GraphStatus;
	impact: GraphImpact;
	summary: string;
	explainer: string;
	evidence: {
		source: string;
		sampled_at: string;
		freshness: string;
	};
	links: Array<{
		label: string;
		target: string;
	}>;
}

interface GraphEdge {
	from: string;
	to: string;
	kind: "causal";
	status: GraphStatus;
	summary: string;
}

interface GraphStatusFixture {
	environment: string;
	instance: string;
	generated_at: string;
	status: GraphStatus;
	impact: GraphImpact;
	nodes: GraphNode[];
	edges: GraphEdge[];
}

const generatedAt = "2026-05-14T00:00:00Z";
const freshEvidence = (source: string): GraphNode["evidence"] => ({
	source,
	sampled_at: generatedAt,
	freshness: "fresh",
});
const graphNode = (
	id: string,
	label: string,
	domain: string,
	status: GraphStatus,
	impact: GraphImpact,
	summary: string,
	explainer: string,
	evidenceSource: string,
	links: GraphNode["links"] = [],
): GraphNode => ({
	id,
	label,
	domain,
	status,
	impact,
	summary,
	explainer,
	evidence: freshEvidence(evidenceSource),
	links,
});
const causalEdge = (
	from: string,
	to: string,
	status: GraphStatus,
	summary: string,
): GraphEdge => ({
	from,
	to,
	kind: "causal",
	status,
	summary,
});

const graphStatus: GraphStatusFixture = {
	environment: "poc",
	instance: "dev.bifrost.midtowntg.com",
	generated_at: generatedAt,
	status: "Degraded",
	impact: "Limited",
	nodes: [
		graphNode(
			"deployment-state",
			"Deployment state",
			"Deployment State",
			"Healthy",
			"None",
			"Live image refs match the infra lock.",
			"Deployment state proves what platform image should be running and whether the live API, client, worker, and scheduler images match infra-pinned refs.",
			"images.lock.yml + deploy guard image refs",
		),
		graphNode(
			"host-runtime",
			"Host runtime",
			"Host Runtime",
			"Healthy",
			"None",
			"The Azure VM and Compose observation completed.",
			"The host runtime is the Azure VM, systemd service, Docker engine, and Compose stack that run this Bifrost instance.",
			"Azure Run Command + bifrost-compose-deploy-guard",
		),
		graphNode(
			"api-readiness",
			"API readiness",
			"API Readiness",
			"Healthy",
			"None",
			"Postgres, Redis, RabbitMQ, and S3 are reachable.",
			"API readiness proves the API can reach its hard dependencies. It does not prove that workers can execute workflow code.",
			"/health/ready",
		),
		graphNode(
			"execution-plane",
			"Execution plane",
			"Execution Plane",
			"Degraded",
			"Limited",
			"Recent infrastructure-shaped execution failures were observed.",
			"The execution plane is the queue, worker, and runtime path that turns workflow requests into completed work.",
			"deploy guard + executions table + RabbitMQ queues",
			[{ label: "Open History", target: "/history" }],
		),
		graphNode(
			"worker-pools",
			"Worker pools",
			"Execution Plane",
			"Healthy",
			"None",
			"1 worker pool heartbeat records observed.",
			"Worker pools pick up queued work and execute workflow code. A heartbeat proves a worker process is alive, but execution outcomes still need aggregate execution health.",
			"worker pool heartbeat table",
			[{ label: "Open History", target: "/history" }],
		),
		graphNode(
			"adjacent-services",
			"Adjacent services",
			"Adjacent Services",
			"Healthy",
			"None",
			"Adjacent service smoke checks passed; optional services disabled: google_ops_worker",
			"Adjacent services are MTG-operated workloads that support Bifrost without being part of the core Compose runtime.",
			"verify-poc-adjacent-services.py",
		),
		graphNode(
			"external-integrations",
			"External integrations",
			"External Integrations",
			"Advisory",
			"None",
			"AutoTask, HaloPSA, NinjaOne, IT Glue, ConnectSecure, Microsoft Graph, Keeper, Cove, and Meraki are advisory unless tied to active work.",
			"External integrations are third-party systems Bifrost talks to frequently. They should inform operator triage without making the core instance look broken unless active workflows are affected.",
			"configured integration probes",
		),
	],
	edges: [
		causalEdge(
			"deployment-state",
			"host-runtime",
			"Healthy",
			"Infra image pins define what the host runtime should run.",
		),
		causalEdge(
			"host-runtime",
			"api-readiness",
			"Healthy",
			"The host and Compose runtime must be alive before API readiness is meaningful.",
		),
		causalEdge(
			"api-readiness",
			"execution-plane",
			"Degraded",
			"API dependencies support the execution plane, but do not prove it is healthy.",
		),
		causalEdge(
			"execution-plane",
			"worker-pools",
			"Degraded",
			"Queued work needs healthy workers to complete.",
		),
		causalEdge(
			"host-runtime",
			"adjacent-services",
			"Healthy",
			"Adjacent services support Bifrost without being core Compose runtime.",
		),
		causalEdge(
			"execution-plane",
			"external-integrations",
			"Advisory",
			"External integrations are advisory until tied to active workflow impact.",
		),
	],
};

const INFRASTRUCTURE_STATUS_URL = "/infrastructure/status.json";

function isGraphStatusFixture(value: unknown): value is GraphStatusFixture {
	if (!value || typeof value !== "object") {
		return false;
	}

	const candidate = value as Partial<GraphStatusFixture>;
	return (
		typeof candidate.environment === "string" &&
		typeof candidate.instance === "string" &&
		typeof candidate.generated_at === "string" &&
		typeof candidate.status === "string" &&
		typeof candidate.impact === "string" &&
		Array.isArray(candidate.nodes) &&
		Array.isArray(candidate.edges)
	);
}

function useInfrastructureStatus() {
	const [status, setStatus] = useState(graphStatus);
	const [source, setSource] = useState<"live" | "fallback">("fallback");
	const [loadError, setLoadError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;

		async function loadStatus() {
			try {
				const response = await fetch(INFRASTRUCTURE_STATUS_URL, {
					cache: "no-store",
					headers: { Accept: "application/json" },
				});

				if (!response.ok) {
					throw new Error(`status endpoint returned ${response.status}`);
				}

				const payload: unknown = await response.json();
				if (!isGraphStatusFixture(payload)) {
					throw new Error("status endpoint returned an unexpected payload");
				}

				if (!cancelled) {
					setStatus(payload);
					setSource("live");
					setLoadError(null);
				}
			} catch (error) {
				if (!cancelled) {
					setStatus(graphStatus);
					setSource("fallback");
					setLoadError(
						error instanceof Error
							? error.message
							: "status endpoint could not be loaded",
					);
				}
			}
		}

		void loadStatus();

		return () => {
			cancelled = true;
		};
	}, []);

	return { status, source, loadError };
}

const statusStyles: Record<GraphStatus, string> = {
	Healthy: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
	Advisory: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
	Degraded: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
	Blocked: "border-destructive/40 bg-destructive/10 text-destructive",
	Unknown: "border-muted-foreground/40 bg-muted text-muted-foreground",
	Disabled: "border-muted-foreground/30 bg-muted/60 text-muted-foreground",
};

const nodeIcons: Record<string, React.ElementType> = {
	"Deployment State": GitBranch,
	"Host Runtime": ServerCog,
	"API Readiness": CheckCircle2,
	"Execution Plane": Activity,
	"Adjacent Services": Network,
	"External Integrations": ExternalLink,
};

function formatTimestamp(value: string): string {
	return new Intl.DateTimeFormat("en-US", {
		month: "short",
		day: "numeric",
		hour: "numeric",
		minute: "2-digit",
		timeZoneName: "short",
	}).format(new Date(value));
}

function StatusBadge({ status }: Readonly<{ status: GraphStatus }>) {
	return (
		<Badge variant="outline" className={cn("shrink-0", statusStyles[status])}>
			{status}
		</Badge>
	);
}

function InfrastructureNode({ node }: Readonly<{ node: GraphNode }>) {
	const Icon = nodeIcons[node.domain] ?? Info;

	return (
		<article
			className="group flex h-full min-h-44 w-full flex-col rounded-lg border bg-background p-4 text-left transition-colors hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			aria-label={`${node.label} ${node.status}`}
		>
			<div className="flex items-start justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
					<div>
						<div className="text-sm font-semibold">{node.label}</div>
						<div className="text-xs text-muted-foreground">{node.domain}</div>
					</div>
				</div>
				<StatusBadge status={node.status} />
			</div>

			<p className="mt-3 text-sm text-muted-foreground">{node.summary}</p>

			<div className="mt-auto space-y-2 pt-4 text-xs text-muted-foreground">
				<div className="flex items-center gap-2">
					<Clock className="h-3.5 w-3.5" />
					<span>{node.evidence.source}</span>
				</div>
				<div className="flex items-center justify-between gap-2">
					<span>{formatTimestamp(node.evidence.sampled_at)}</span>
					<span>{node.impact} impact</span>
				</div>
				{node.links.length > 0 ? (
					<div className="pt-1">
						{node.links.map((link) => (
							<Link
								key={`${node.id}-${link.label}`}
								to={link.target}
								className="inline-flex items-center gap-1 text-primary hover:underline"
							>
								{link.label}
								<ExternalLink className="h-3 w-3" />
							</Link>
						))}
					</div>
				) : null}
			</div>
		</article>
	);
}

function EdgeList({ edges }: Readonly<{ edges: GraphEdge[] }>) {
	return (
		<div className="grid gap-3 lg:grid-cols-2">
			{edges.map((edge) => (
				<div
					key={`${edge.from}-${edge.to}`}
					className="rounded-lg border bg-background p-3"
				>
					<div className="flex items-center justify-between gap-3">
						<div className="text-sm font-medium">
							{edge.from} to {edge.to}
						</div>
						<StatusBadge status={edge.status} />
					</div>
					<p className="mt-2 text-sm text-muted-foreground">
						{edge.summary}
					</p>
				</div>
			))}
		</div>
	);
}

export function InfrastructureStatus() {
	const { status: infrastructureStatus, source, loadError } =
		useInfrastructureStatus();
	const degradedNodes = infrastructureStatus.nodes.filter(
		(node) => node.status === "Degraded" || node.status === "Blocked",
	);
	const blockedCount = degradedNodes.filter(
		(node) => node.status === "Blocked",
	).length;
	const degradedCount = degradedNodes.filter(
		(node) => node.status === "Degraded",
	).length;
	const blockedSummary = `${blockedCount} ${
		blockedCount === 1 ? "domain is" : "domains are"
	} blocked`;
	const degradedSummary = `${degradedCount} ${
		degradedCount === 1 ? "domain is" : "domains are"
	} degraded`;
	const attentionDescription =
		blockedCount > 0
			? `${blockedSummary} and ${degradedSummary}.`
			: `The instance is not blocked, but ${degradedSummary}.`;

	return (
		<div className="space-y-6">
			<div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="space-y-2">
					<div className="flex flex-wrap items-center gap-2">
						<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
							Infrastructure Status
						</h1>
						<StatusBadge status={infrastructureStatus.status} />
					</div>
					<p className="max-w-3xl leading-7 text-muted-foreground">
						Read-only instance map for runtime health, pressure,
						dependencies, evidence, and next inspection points.
					</p>
				</div>
				<div className="rounded-lg border bg-muted/30 p-4 text-sm">
					<div className="font-medium">{infrastructureStatus.instance}</div>
					<div className="mt-1 text-muted-foreground">
						{infrastructureStatus.environment} environment
					</div>
					<div className="mt-3 flex flex-wrap gap-2">
						<Badge variant="outline">
							{infrastructureStatus.impact} impact
						</Badge>
						<Badge variant="outline">
							Sampled {formatTimestamp(infrastructureStatus.generated_at)}
						</Badge>
						<Badge variant={source === "live" ? "secondary" : "outline"}>
							{source === "live" ? "Live feed" : "Fallback snapshot"}
						</Badge>
					</div>
				</div>
			</div>

			{loadError ? (
				<Card className="border-sky-500/40 bg-sky-500/5">
					<CardHeader className="pb-3">
						<CardTitle className="flex items-center gap-2 text-base">
							<Info className="h-4 w-4 text-sky-600" />
							Using fallback snapshot
						</CardTitle>
						<CardDescription>
							Live infrastructure status could not be loaded: {loadError}.
						</CardDescription>
					</CardHeader>
				</Card>
			) : null}

			{degradedNodes.length > 0 ? (
				<Card className="border-amber-500/40 bg-amber-500/5">
					<CardHeader className="pb-3">
						<CardTitle className="flex items-center gap-2 text-base">
							<AlertTriangle className="h-4 w-4 text-amber-600" />
							Needs operator attention
						</CardTitle>
						<CardDescription>{attentionDescription}</CardDescription>
					</CardHeader>
					<CardContent className="space-y-2">
						{degradedNodes.map((node) => (
							<div key={node.id} className="text-sm">
								<span className="font-medium">{node.label}:</span>{" "}
								<span className="text-muted-foreground">
									{node.summary}
								</span>
							</div>
						))}
					</CardContent>
				</Card>
			) : null}

			<section className="space-y-3" aria-label="Infrastructure graph nodes">
				<div className="flex items-center justify-between">
					<h2 className="text-xl font-semibold">Instance Map</h2>
					<Badge variant="secondary">Read-only</Badge>
				</div>
				<div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
					{infrastructureStatus.nodes.map((node) => (
						<InfrastructureNode key={node.id} node={node} />
					))}
				</div>
			</section>

			<section className="space-y-3" aria-label="Causal dependencies">
				<h2 className="text-xl font-semibold">Causal Edges</h2>
				<EdgeList edges={infrastructureStatus.edges} />
			</section>

			<section className="space-y-3" aria-label="Operator notes">
				<h2 className="text-xl font-semibold">What Each Layer Proves</h2>
				<div className="grid gap-4 lg:grid-cols-2">
					{infrastructureStatus.nodes.map((node) => (
						<Card key={`${node.id}-details`}>
							<CardHeader className="pb-3">
								<CardTitle className="flex items-center justify-between gap-3 text-base">
									<span>{node.label}</span>
									<StatusBadge status={node.status} />
								</CardTitle>
								<CardDescription>{node.evidence.source}</CardDescription>
							</CardHeader>
							<CardContent className="space-y-3 text-sm text-muted-foreground">
								<p>{node.explainer}</p>
								<div className="flex flex-wrap gap-2">
									<Badge variant="outline">{node.evidence.freshness}</Badge>
									<Badge variant="outline">{node.impact} impact</Badge>
								</div>
								{node.status === "Degraded" ? (
									<div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-amber-700 dark:text-amber-300">
										<ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
										<span>
											Use this page to identify the layer, then open the
											existing investigation surface for row-level detail.
										</span>
									</div>
								) : null}
							</CardContent>
						</Card>
					))}
				</div>
			</section>

			<div className="flex flex-wrap gap-2">
				<Button asChild variant="outline">
					<Link to="/history">Open History</Link>
				</Button>
				<Button asChild variant="outline">
					<Link to="/diagnostics">Open Diagnostics</Link>
				</Button>
			</div>
		</div>
	);
}
