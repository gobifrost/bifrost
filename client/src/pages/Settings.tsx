import {
	createElement,
	type ComponentType,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import { useNavigate, useLocation } from "react-router-dom";
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
import { BuilderSettings } from "@/pages/settings/Builder";
import { cn } from "@/lib/utils";
import {
	Bot,
	BrainCircuit,
	ChevronDown,
	Database,
	DollarSign,
	Key,
	Layers3,
	MessageSquareText,
	Palette,
	Plug,
	ScrollText,
	Shield,
	Sparkles,
	Wrench,
	type LucideIcon,
} from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";

interface SettingsContentProps {
	canWrite?: boolean;
	canExecute?: boolean;
}

type SettingsItem = {
	value: string;
	label: string;
	icon: ComponentType<{ className?: string }>;
	content: ComponentType<SettingsContentProps>;
};

type SettingsSection = {
	id: string;
	label: string;
	icon: LucideIcon;
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
				value: "builder",
				label: "Builder",
				icon: Sparkles,
				content: BuilderSettings,
			},
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

const SETTINGS_CAPABILITIES: Record<
	string,
	{ read: string; write: string }
> = {
	ai: { read: "configs.read", write: "configs.readwrite" },
	"ai-embeddings": { read: "configs.read", write: "configs.readwrite" },
	"ai-chat": { read: "configs.read", write: "configs.readwrite" },
	"ai-memory": { read: "configs.read", write: "configs.readwrite" },
	"ai-instructions": { read: "configs.read", write: "configs.readwrite" },
	"ai-usage": { read: "configs.read", write: "configs.readwrite" },
	mcp: { read: "integrations.read", write: "integrations.readwrite" },
	github: { read: "repository.read", write: "repository.readwrite" },
	sso: { read: "integrations.read", write: "integrations.readwrite" },
	"workflow-keys": { read: "workflows.read", write: "workflows.readwrite" },
	builder: { read: "platformjobs.read", write: "platformjobs.execute" },
	branding: { read: "configs.read", write: "configs.readwrite" },
	maintenance: { read: "platformjobs.read", write: "platformjobs.execute" },
};

function findActiveSectionId(
	sections: SettingsSection[],
	currentTab: string,
) {
	return (
		sections.find((section) =>
			section.items.some((item) => item.value === currentTab),
		)?.id ?? sections[0]?.id ?? ""
	);
}

export function Settings() {
	const navigate = useNavigate();
	const location = useLocation();
	const { hasSelectedCapability } = useAuthorizationBoundary();
	const availableSections = settingsSections
		.map((section) => ({
			...section,
			items: section.items.filter((item) => {
				const capabilities = SETTINGS_CAPABILITIES[item.value];
				return (
					capabilities !== undefined &&
					(hasSelectedCapability(capabilities.read) ||
						hasSelectedCapability(capabilities.write))
				);
			}),
		}))
		.filter((section) => section.items.length > 0);

	// Parse the current tab from the URL path
	const requestedTab = location.pathname.split("/settings/")[1];
	const availableItems = availableSections.flatMap((section) => section.items);
	const activeItem =
		availableItems.find((item) => item.value === requestedTab) ??
		availableItems[0];
	const currentTab = activeItem?.value ?? "";
	const activeSectionId = findActiveSectionId(availableSections, currentTab);
	const ActiveContent = activeItem?.content;
	const [expandedSections, setExpandedSections] = useState<string[]>(() => [
		activeSectionId,
	]);
	const contentRef = useRef<HTMLElement>(null);

	const sectionState = useMemo(
		() => new Set(expandedSections),
		[expandedSections],
	);

	const handleRouteChange = (value: string) => {
		const destinationSection = findActiveSectionId(availableSections, value);
		setExpandedSections((sections) =>
			sections.includes(destinationSection)
				? sections
				: [...sections, destinationSection],
		);
		navigate(`/settings/${value}`);
	};

	const toggleSection = (sectionId: string) => {
		setExpandedSections((sections) =>
			sections.includes(sectionId)
				? sections.filter((id) => id !== sectionId)
				: [...sections, sectionId],
		);
	};

	// Keep the URL on the first setting admitted by the selected Role.
	useEffect(() => {
		if (currentTab && location.pathname !== `/settings/${currentTab}`) {
			navigate(`/settings/${currentTab}`, { replace: true });
		}
	}, [currentTab, location.pathname, navigate]);

	useEffect(() => {
		if (contentRef.current) contentRef.current.scrollTop = 0;
	}, [currentTab]);

	return (
		<div className="mx-auto flex h-full min-h-0 w-full max-w-7xl flex-col space-y-6 px-4 sm:px-6 lg:px-8">
			<div className="max-w-3xl">
				<h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
					Settings
				</h1>
				<p className="mt-2 text-muted-foreground">
					Manage platform settings and configuration
				</p>
			</div>

			<div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
				<nav
					aria-label="Settings sections"
					className="min-h-0 rounded-lg border bg-card p-2 lg:max-h-[calc(100vh-13rem)] lg:overflow-auto"
				>
					{availableSections.map((section) => {
						const SectionIcon = section.icon;
						const isExpanded = sectionState.has(section.id);
						const containsActive = section.id === activeSectionId;

						return (
							<div key={section.id} className="space-y-1">
								<button
									type="button"
									aria-expanded={isExpanded}
									aria-controls={`settings-section-${section.id}`}
									onClick={() => toggleSection(section.id)}
									className={cn(
										"flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
										containsActive && "text-foreground",
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
											"h-4 w-4 shrink-0 transition-transform",
											!isExpanded && "-rotate-90",
										)}
										aria-hidden="true"
									/>
								</button>

								{isExpanded && (
									<div
										id={`settings-section-${section.id}`}
										className="space-y-1 pb-2 pl-3"
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
														"flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
														isActive
															? "bg-accent font-medium text-accent-foreground"
															: "text-muted-foreground",
													)}
												>
													<ItemIcon className="h-4 w-4 shrink-0" />
													<span>{item.label}</span>
												</button>
											);
										})}
									</div>
								)}
							</div>
						);
					})}
				</nav>

				<section
					ref={contentRef}
					className="min-h-0 overflow-auto px-1 pb-6 pr-3 sm:px-2 sm:pr-4"
				>
					{ActiveContent && activeItem
						? createElement(ActiveContent, {
								canWrite: hasSelectedCapability(
									SETTINGS_CAPABILITIES[activeItem.value]
										.write,
								),
								canExecute: hasSelectedCapability(
									SETTINGS_CAPABILITIES[activeItem.value]
										.write,
								),
							})
						: null}
				</section>
			</div>
		</div>
	);
}
