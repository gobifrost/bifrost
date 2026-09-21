import { AgentQualityWorkbench } from "./AgentQualityWorkbench";

/** Keeps the legacy fleet URL stable while sharing the Workbench route owner. */
export function GlobalAgentQualityPage() {
	return <AgentQualityWorkbench scope="fleet" />;
}

export default GlobalAgentQualityPage;
