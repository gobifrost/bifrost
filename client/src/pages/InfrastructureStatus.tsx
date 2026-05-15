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

const graphStatus: GraphStatusFixture = {
	environment: "poc",
	instance: "dev.bifrost.midtowntg.com",
	generated_at: "2026-05-14T00:00:00Z",
	status: "Degraded",
	impact: "Limited",
	nodes: [
		{
			id: "deployment-state",
			label: "Deployment state",
			domain: "Deployment State",
			status: "Healthy",
			impact: "None",
			summary: "Live image refs match the infra lock.",
			explainer:
				"Deployment state proves what platform image should be running and whether the live API, client, worker, and scheduler images match infra-pinned refs.",
			evidence: {
				source: "images.lock.yml + deploy guard image refs",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [],
		},
		{
			id: "host-runtime",
			label: "Host runtime",
			domain: "Host Runtime",
			status: "Healthy",
			impact: "None",
			summary: "The Azure VM and Compose observation completed.",
			explainer:
				"The host runtime is the Azure VM, systemd service, Docker engine, and Compose stack that run this Bifrost instance.",
			evidence: {
				source: "Azure Run Command + bifrost-compose-deploy-guard",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [],
		},
		{
			id: "api-readiness",
			label: "API readiness",
			domain: "API Readiness",
			status: "Healthy",
			impact: "None",
			summary: "Postgres, Redis, RabbitMQ, and S3 are reachable.",
			explainer:
				"API readiness proves the API can reach its hard dependencies. It does not prove that workers can execute workflow code.",
			evidence: {
				source: "/health/ready",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [],
		},
		{
			id: "execution-plane",
			label: "Execution plane",
			domain: "Execution Plane",
			status: "Degraded",
			impact: "Limited",
			summary: "Recent infrastructure-shaped execution failures were observed.",
			explainer:
				"The execution plane is the queue, worker, and runtime path that turns workflow requests into completed work.",
			evidence: {
				source: "deploy guard + executions table + RabbitMQ queues",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [{ label: "Open History", target: "/history" }],
		},
		{
			id: "worker-pools",
			label: "Worker pools",
			domain: "Execution Plane",
			status: "Healthy",
			impact: "None",
			summary: "1 worker pool heartbeat records observed.",
			explainer:
				"Worker pools pick up queued work and execute workflow code. A heartbeat proves a worker process is alive, but execution outcomes still need aggregate execution health.",
			evidence: {
				source: "worker pool heartbeat table",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [{ label: "Open History", target: "/history" }],
		},
		{
			id: "adjacent-services",
			label: "Adjacent services",
			domain: "Adjacent Services",
			status: "Healthy",
			impact: "None",
			summary:
				"Adjacent service smoke checks passed; optional services disabled: google_ops_worker",
			explainer:
				"Adjacent services are MTG-operated workloads that support Bifrost without being part of the core Compose runtime.",
			evidence: {
				source: "verify-poc-adjacent-services.py",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [],
		},
		{
			id: "external-integrations",
			label: "External integrations",
			domain: "External Integrations",
			status: "Advisory",
			impact: "None",
			summary:
				"AutoTask, HaloPSA, NinjaOne, IT Glue, ConnectSecure, Microsoft Graph, Keeper, Cove, and Meraki are advisory unless tied to active work.",
			explainer:
				"External integrations are third-party systems Bifrost talks to frequently. They should inform operator triage without making the core instance look broken unless active workflows are affected.",
			evidence: {
				source: "configured integration probes",
				sampled_at: "2026-05-14T00:00:00Z",
				freshness: "fresh",
			},
			links: [],
		},
	],
	edges: [
		{
			from: "deployment-state",
			to: "host-runtime",
			kind: "causal",
			status: "Healthy",
			summary: "Infra image pins define what the host runtime should run.",
		},
		{
			from: "host-runtime",
			to: "api-readiness",
			kind: "causal",
			status: "Healthy",
			summary:
				"The host and Compose runtime must be alive before API readiness is meaningful.",
		},
		{
			from: "api-readiness",
			to: "execution-plane",
			kind: "causal",
			status: "Degraded",
			summary:
				"API dependencies support the execution plane, but do not prove it is healthy.",
		},
		{
			from: "execution-plane",
			to: "worker-pools",
			kind: "causal",
			status: "Degraded",
			summary: "Queued work needs healthy workers to complete.",
		},
		{
			from: "host-runtime",
			to: "adjacent-services",
			kind: "causal",
			status: "Healthy",
			summary:
				"Adjacent services support Bifrost without being core Compose runtime.",
		},
		{
			from: "execution-plane",
			to: "external-integrations",
			kind: "causal",
			status: "Advisory",
			summary:
				"External integrations are advisory until tied to active workflow impact.",
		},
	],
};

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

function StatusBadge({ status }: { status: GraphStatus }) {
	return (
		<Badge variant="outline" className={cn("shrink-0", statusStyles[status])}>
			{status}
		</Badge>
	);
}

function InfrastructureNode({ node }: { node: GraphNode }) {
	const Icon = nodeIcons[node.domain] ?? Info;

	return (
		<button
			type="button"
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
		</button>
	);
}

function EdgeList({ edges }: { edges: GraphEdge[] }) {
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
	const degradedNodes = graphStatus.nodes.filter(
		(node) => node.status === "Degraded" || node.status === "Blocked",
	);

	return (
		<div className="space-y-6">
			<div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="space-y-2">
					<div className="flex flex-wrap items-center gap-2">
						<h1 className="scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl">
							Infrastructure Status
						</h1>
						<StatusBadge status={graphStatus.status} />
					</div>
					<p className="max-w-3xl leading-7 text-muted-foreground">
						Read-only instance map for runtime health, pressure,
						dependencies, evidence, and next inspection points.
					</p>
				</div>
				<div className="rounded-lg border bg-muted/30 p-4 text-sm">
					<div className="font-medium">{graphStatus.instance}</div>
					<div className="mt-1 text-muted-foreground">
						{graphStatus.environment} environment
					</div>
					<div className="mt-3 flex flex-wrap gap-2">
						<Badge variant="outline">{graphStatus.impact} impact</Badge>
						<Badge variant="outline">
							Sampled {formatTimestamp(graphStatus.generated_at)}
						</Badge>
					</div>
				</div>
			</div>

			{degradedNodes.length > 0 ? (
				<Card className="border-amber-500/40 bg-amber-500/5">
					<CardHeader className="pb-3">
						<CardTitle className="flex items-center gap-2 text-base">
							<AlertTriangle className="h-4 w-4 text-amber-600" />
							Needs operator attention
						</CardTitle>
						<CardDescription>
							The instance is not blocked, but one domain is degraded.
						</CardDescription>
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
					{graphStatus.nodes.map((node) => (
						<InfrastructureNode key={node.id} node={node} />
					))}
				</div>
			</section>

			<section className="space-y-3" aria-label="Causal dependencies">
				<h2 className="text-xl font-semibold">Causal Edges</h2>
				<EdgeList edges={graphStatus.edges} />
			</section>

			<section className="space-y-3" aria-label="Operator notes">
				<h2 className="text-xl font-semibold">What Each Layer Proves</h2>
				<div className="grid gap-4 lg:grid-cols-2">
					{graphStatus.nodes.map((node) => (
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
