export type BuilderWorkbenchTab = "preview" | "code" | "changes";
export type BuilderMobilePane = "agent" | BuilderWorkbenchTab;
export type BuilderPreviewDevice =
	"responsive" | "desktop" | "tablet" | "mobile";

export interface BuilderWorkbenchState {
	activeSessionId: string | null;
	workbenchTab: BuilderWorkbenchTab;
	mobilePane: BuilderMobilePane;
	agentPanelWidth: number;
	previewRoute: string;
	previewDevice: BuilderPreviewDevice;
}

export const DEFAULT_BUILDER_WORKBENCH_STATE: BuilderWorkbenchState = {
	activeSessionId: null,
	workbenchTab: "preview",
	mobilePane: "agent",
	agentPanelWidth: 42,
	previewRoute: "/",
	previewDevice: "responsive",
};

const STORAGE_PREFIX = "bifrost:builder-workbench:v1:";
const WORKBENCH_TABS = new Set<BuilderWorkbenchTab>([
	"preview",
	"code",
	"changes",
]);
const MOBILE_PANES = new Set<BuilderMobilePane>([
	"agent",
	"preview",
	"code",
	"changes",
]);
const PREVIEW_DEVICES = new Set<BuilderPreviewDevice>([
	"responsive",
	"desktop",
	"tablet",
	"mobile",
]);

function storageKey(solutionId: string): string {
	return `${STORAGE_PREFIX}${solutionId}`;
}

function storageOrNull(storage?: Storage): Storage | null {
	if (storage) return storage;
	if (typeof window === "undefined") return null;
	return window.localStorage;
}

export function loadBuilderWorkbenchState(
	solutionId: string,
	storage?: Storage,
): BuilderWorkbenchState {
	const target = storageOrNull(storage);
	if (!solutionId || !target) return DEFAULT_BUILDER_WORKBENCH_STATE;

	try {
		const raw = target.getItem(storageKey(solutionId));
		if (!raw) return DEFAULT_BUILDER_WORKBENCH_STATE;
		const saved = JSON.parse(raw) as Partial<BuilderWorkbenchState>;

		return {
			activeSessionId:
				typeof saved.activeSessionId === "string"
					? saved.activeSessionId
					: null,
			workbenchTab: WORKBENCH_TABS.has(
				saved.workbenchTab as BuilderWorkbenchTab,
			)
				? (saved.workbenchTab as BuilderWorkbenchTab)
				: DEFAULT_BUILDER_WORKBENCH_STATE.workbenchTab,
			mobilePane: MOBILE_PANES.has(saved.mobilePane as BuilderMobilePane)
				? (saved.mobilePane as BuilderMobilePane)
				: DEFAULT_BUILDER_WORKBENCH_STATE.mobilePane,
			agentPanelWidth:
				typeof saved.agentPanelWidth === "number" &&
				Number.isFinite(saved.agentPanelWidth)
					? Math.min(58, Math.max(32, saved.agentPanelWidth))
					: DEFAULT_BUILDER_WORKBENCH_STATE.agentPanelWidth,
			previewRoute:
				typeof saved.previewRoute === "string" &&
				saved.previewRoute.startsWith("/")
					? saved.previewRoute
					: DEFAULT_BUILDER_WORKBENCH_STATE.previewRoute,
			previewDevice: PREVIEW_DEVICES.has(
				saved.previewDevice as BuilderPreviewDevice,
			)
				? (saved.previewDevice as BuilderPreviewDevice)
				: DEFAULT_BUILDER_WORKBENCH_STATE.previewDevice,
		};
	} catch {
		return DEFAULT_BUILDER_WORKBENCH_STATE;
	}
}

export function saveBuilderWorkbenchState(
	solutionId: string,
	state: BuilderWorkbenchState,
	storage?: Storage,
): void {
	const target = storageOrNull(storage);
	if (!solutionId || !target) return;

	try {
		target.setItem(storageKey(solutionId), JSON.stringify(state));
	} catch {
		// Workbench persistence is a convenience. Storage can be disabled or
		// full without making the builder itself unusable.
	}
}
