import {
	createElement,
	type ComponentType,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { navigationSelectionClasses } from "@/components/layout/navigationStyles";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { Button } from "@/components/ui/button";
import { WorkflowKeys } from "@/pages/settings/WorkflowKeys";
import { Branding } from "@/pages/settings/Branding";
import { OAuth } from "@/pages/settings/OAuth";
import { GitHub } from "@/pages/settings/GitHub";
import { AIModelSettings } from "@/pages/settings/AIModelSettings";
import { AIEmbeddingSettings } from "@/pages/settings/AIEmbeddingSettings";
import { AIBehaviorSettings } from "@/pages/settings/AIBehaviorSettings";
import { AIUsageSettings } from "@/pages/settings/AIUsageSettings";
import { MemorySettings } from "@/pages/settings/MemorySettings";
import { RequiredInstructionsSettings } from "@/pages/settings/RequiredInstructionsSettings";
import { MCP } from "@/pages/settings/MCP";
import { Maintenance } from "@/pages/settings/Maintenance";
import { KubernetesExecutions } from "@/pages/settings/KubernetesExecutions";
import { getKubernetesStatus } from "@/services/kubernetes";
import { cn } from "@/lib/utils";
import {
	Bot,
	BrainCircuit,
	ChevronDown,
	Container,
	Database,
	DollarSign,
	Key,
	Layers3,
	MessageSquareText,
	Palette,
	Plug,
	ScrollText,
	Shield,
	Wrench,
} from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import { KubernetesIcon } from "@/components/icons/KubernetesIcon";

type SettingsItem = {
	value: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
	content: ComponentType;
};

type SettingsSection = {
	id: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
	items: SettingsItem[];
};

const settingsSections: SettingsSection[] = [
	{
		id: "ai",
		label: "AI",
		icon: Bot,
		items: [
			{
				value: "ai",
				label: "Models",
				icon: Layers3,
				content: AIModelSettings,
			},
			{
				value: "ai-embeddings",
				label: "Embeddings",
				icon: Database,
				content: AIEmbeddingSettings,
			},
			{
				value: "ai-chat",
				label: "Chat Instructions",
				icon: MessageSquareText,
				content: AIBehaviorSettings,
			},
			{
				value: "ai-memory",
				label: "Memory",
				icon: BrainCircuit,
				content: MemorySettings,
			},
			{
				value: "ai-instructions",
				label: "Default MCP Instructions",
				icon: ScrollText,
				content: RequiredInstructionsSettings,
			},
			{
				value: "ai-usage",
				label: "Usage & Pricing",
				icon: DollarSign,
				content: AIUsageSettings,
			},
		],
	},
	{
		id: "connections",
		label: "Connections",
		icon: Plug,
		items: [
			{ value: "mcp", label: "MCP", icon: Plug, content: MCP },
			{ value: "github", label: "GitHub", icon: Github, content: GitHub },
		],
	},
	{
		id: "security",
		label: "Security",
		icon: Shield,
		items: [
			{
				value: "sso",
				label: "Authentication",
				icon: Shield,
				content: OAuth,
			},
			{
				value: "workflow-keys",
				label: "Workflow Keys",
				icon: Key,
				content: WorkflowKeys,
			},
		],
	},
	{
		id: "platform",
		label: "Platform",
		icon: Palette,
		items: [
			{
				value: "branding",
				label: "Branding",
				icon: Palette,
				content: Branding,
			},
			{
				value: "maintenance",
				label: "Maintenance",
				icon: Wrench,
				content: Maintenance,
			},
		],
	},
];

function findActiveSectionId(
	sections: SettingsSection[],
	currentTab: string,
) {
	return (
		sections.find((section) =>
			section.items.some((item) => item.value === currentTab),
		)?.id ?? sections[0].id
	);
}

const kubernetesSection: SettingsSection = {
	id: "kubernetes",
	label: "Kubernetes",
	icon: KubernetesIcon,
		items: [
			{
				value: "kubernetes-executions",
				label: "Executions",
				icon: Container,
				content: KubernetesExecutions,
			},
		],
};

export function Settings() {
	const navigate = useNavigate();
	const location = useLocation();
	const [kubernetesAvailable, setKubernetesAvailable] = useState(false);

	// The Kubernetes section appears only when the deployment configured
	// the remote-build backend. It is a second home for K8s options later
	// (e.g. built-in monitoring) beside today's Executions toggles.
	useEffect(() => {
		let active = true;
		getKubernetesStatus()
			.then((status) => {
				if (active) setKubernetesAvailable(status.configured);
			})
			.catch(() => {
				if (active) setKubernetesAvailable(false);
			});
		return () => {
			active = false;
		};
	}, []);

	const sections = useMemo(
		() =>
			kubernetesAvailable
				? [...settingsSections, kubernetesSection]
				: settingsSections,
		[kubernetesAvailable],
	);

	// Parse the current tab from the URL path
	const requestedTab = location.pathname.split("/settings/")[1];
	const currentTab = sections.some((section) =>
		section.items.some((item) => item.value === requestedTab),
	)
		? requestedTab
		: "ai";
	const activeSectionId = findActiveSectionId(sections, currentTab);
	const [expandedSections, setExpandedSections] = useState<string[]>(() => [
		activeSectionId,
	]);
	const [visitedTabs, setVisitedTabs] = useState(() => new Set([currentTab]));
	const [previousTab, setPreviousTab] = useState(currentTab);
	if (previousTab !== currentTab) {
		setPreviousTab(currentTab);
		setVisitedTabs((tabs) => new Set([...tabs, currentTab]));
		setExpandedSections((sections) =>
			sections.includes(activeSectionId)
				? sections
				: [...sections, activeSectionId],
		);
	}
	const contentRef = useRef<HTMLDivElement>(null);
	const mobileNavigationRef = useRef<HTMLButtonElement>(null);
	const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
	const activeLabel =
		sections
			.flatMap((section) => section.items)
			.find((item) => item.value === currentTab)?.label ?? "Models";

	const sectionState = useMemo(
		() => new Set(expandedSections),
		[expandedSections],
	);

	const handleRouteChange = (value: string) => {
		const destinationSection = findActiveSectionId(sections, value);
		setExpandedSections((sections) =>
			sections.includes(destinationSection)
				? sections
				: [...sections, destinationSection],
		);
		if (mobileNavigationOpen) mobileNavigationRef.current?.focus();
		setMobileNavigationOpen(false);
		navigate(`/settings/${value}`);
	};

	const toggleSection = (sectionId: string) => {
		setExpandedSections((sections) =>
			sections.includes(sectionId)
				? sections.filter((id) => id !== sectionId)
				: [...sections, sectionId],
		);
	};

	// Redirect /settings to /settings/ai (first tab)
	useEffect(() => {
		if (requestedTab !== currentTab) {
			navigate(`/settings/${currentTab}`, { replace: true });
		}
	}, [requestedTab, currentTab, navigate]);

	useEffect(() => {
		if (contentRef.current) contentRef.current.scrollTop = 0;
	}, [currentTab]);

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl">
			<ListPageHeader
				title="Settings"
				description="Manage platform settings and configuration"
			/>
			<div className="flex min-h-0 flex-col overflow-hidden rounded-[var(--bf-radius-feature)] border border-border/70 bg-card lg:flex-1">
				<Button
					variant="ghost"
					className="h-auto min-h-12 w-full shrink-0 justify-between gap-3 rounded-none border-b border-border/70 bg-muted/20 whitespace-normal px-4 py-3 text-left lg:hidden"
					aria-label={`Settings navigation: ${activeLabel}`}
					aria-expanded={mobileNavigationOpen}
					ref={mobileNavigationRef}
					aria-controls="settings-navigation"
					onClick={() => setMobileNavigationOpen((open) => !open)}
				>
					<span className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
						<span className="block text-xs font-normal text-muted-foreground">
							Settings navigation
						</span>
						<span className="block">{activeLabel}</span>
					</span>
					<ChevronDown
						className={cn(
							"size-4 shrink-0 transition-transform duration-[var(--bf-motion-disclosure)] motion-reduce:transition-none",
							mobileNavigationOpen && "rotate-180",
						)}
					/>
				</Button>

				<div className="grid min-h-0 lg:flex-1 lg:grid-cols-[15rem_minmax(0,1fr)]">
					<nav
						id="settings-navigation"
						aria-label="Settings sections"
						className={cn(
							"min-h-0 space-y-2 border-b border-border/70 bg-muted/20 p-3 lg:block lg:overflow-auto lg:border-b-0 lg:border-r",
							!mobileNavigationOpen && "hidden",
						)}
					>
						{sections.map((section) => {
							const SectionIcon = section.icon;
							const isExpanded = sectionState.has(section.id);
							const containsActive =
								section.id === activeSectionId;

							return (
								<div key={section.id} className="space-y-1">
									<button
										type="button"
										aria-expanded={isExpanded}
										aria-controls={`settings-section-${section.id}`}
										onClick={() =>
											toggleSection(section.id)
										}
										className={cn(
											"flex min-h-11 w-full items-center gap-2 rounded-none border-l-2 border-transparent px-3 py-2 text-left text-sm font-medium transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:bg-accent hover:text-accent-foreground focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
											containsActive && "text-primary",
											!containsActive &&
												"text-muted-foreground",
										)}
									>
										<SectionIcon className="h-4 w-4 shrink-0" />
										<span className="flex-1">
											{section.label}
										</span>
										<ChevronDown
											className={cn(
												"h-4 w-4 shrink-0 transition-transform duration-[var(--bf-motion-disclosure)] motion-reduce:transition-none",
												!isExpanded && "-rotate-90",
											)}
											aria-hidden="true"
										/>
									</button>

									{isExpanded && (
										<div
											id={`settings-section-${section.id}`}
											className="space-y-1 pb-2"
										>
											{section.items.map((item) => {
												const ItemIcon = item.icon;
												const isActive =
													item.value === currentTab;

												return (
													<button
														key={item.value}
														type="button"
														aria-current={
															isActive
																? "page"
																: undefined
														}
														onClick={() =>
															handleRouteChange(
																item.value,
															)
														}
														className={cn(
															"flex min-h-11 w-full items-center gap-2 py-2 pl-9 pr-3 text-left text-sm transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:bg-accent hover:text-accent-foreground focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
															navigationSelectionClasses(
																isActive,
															),
															"font-medium",
														)}
													>
														<ItemIcon className="h-4 w-4 shrink-0" />
														<span>
															{item.label}
														</span>
													</button>
												);
											})}
										</div>
									)}
								</div>
							);
						})}
					</nav>

					<section className="min-w-0 lg:flex lg:min-h-0 lg:flex-col">
						<div
							ref={contentRef}
							data-page-scroll
							className="min-w-0 p-4 sm:p-6 lg:min-h-0 lg:flex-1 lg:overflow-auto"
						>
							{sections
								.flatMap((section) => section.items)
								.filter((item) => visitedTabs.has(item.value))
								.map((item) => (
									<div
										key={item.value}
										hidden={item.value !== currentTab}
									>
										{createElement(item.content)}
									</div>
								))}
						</div>
					</section>
				</div>
			</div>
		</PageWorkspace>
	);
}
