import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SolutionSetupWizard } from "./SolutionSetupWizard";
import type { SolutionSetupItem } from "@/services/solutions";

const configItem: SolutionSetupItem = {
	key: "API_URL",
	type: "string",
	required: true,
	is_set: false,
	kind: "config",
	description: null,
	default: null,
	has_oauth: false,
	connected: false,
};

const oauthConnection: SolutionSetupItem = {
	key: "HaloPSA",
	type: "integration",
	required: true,
	is_set: false,
	kind: "connection",
	description: null,
	default: null,
	has_oauth: true,
	connected: false,
};

const endpointKey: SolutionSetupItem = {
	key: "22222222-2222-2222-2222-222222222222",
	type: "workflow_endpoint_key",
	required: true,
	is_set: false,
	kind: "workflow_endpoint_key",
	description: null,
	default: null,
	has_oauth: false,
	connected: false,
	workflow_id: "22222222-2222-2222-2222-222222222222",
	workflow_name: "Ticket Webhook",
	allowed_methods: ["POST"],
};

describe("SolutionSetupWizard", () => {
	it("warns about OAuth on a connection but does not gate completion on it", async () => {
		render(
			<SolutionSetupWizard
				items={[configItem, oauthConnection]}
				setupComplete={false}
				onSetConfig={vi.fn()}
				integrationHref={() => "/integrations"}
			/>,
		);

		// Step 1 (configs) renders the config item.
		expect(screen.getByText("API_URL")).toBeInTheDocument();

		// Advance to the connections step.
		await userEvent.click(screen.getByRole("button", { name: /next/i }));

		// Connection warning present and warn-only labeled.
		expect(screen.getByText(/uses OAuth/i)).toBeInTheDocument();

		// "Set up integration" link present, opening a new tab.
		const link = screen.getByRole("link", { name: /set up integration/i });
		expect(link).toBeInTheDocument();
		expect(link).toHaveAttribute("target", "_blank");

		// Finish button is enabled even though the OAuth warning is showing.
		expect(screen.getByRole("button", { name: /finish|done/i })).toBeEnabled();
	});

	it("is a single config step when there are no connection items", () => {
		render(
			<SolutionSetupWizard
				items={[configItem]}
				setupComplete={false}
				onSetConfig={vi.fn()}
			/>,
		);
		expect(screen.getByText("API_URL")).toBeInTheDocument();
		// No Next button — only a single step.
		expect(
			screen.queryByRole("button", { name: /next/i }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: /finish|done/i })).toBeEnabled();
	});

	it("starts on the connections step when there are no config items", () => {
		render(
			<SolutionSetupWizard
				items={[oauthConnection]}
				setupComplete={false}
				onSetConfig={vi.fn()}
			/>,
		);
		expect(
			screen.getByRole("link", { name: /set up integration/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /next/i }),
		).not.toBeInTheDocument();
	});

	it("calls onFinish when Finish is clicked", async () => {
		const onFinish = vi.fn();
		render(
			<SolutionSetupWizard
				items={[configItem]}
				setupComplete={true}
				onSetConfig={vi.fn()}
				onFinish={onFinish}
			/>,
		);
		await userEvent.click(screen.getByRole("button", { name: /finish|done/i }));
		expect(onFinish).toHaveBeenCalled();
	});

	it("includes workflow endpoint keys as their own setup step", async () => {
		const onGenerateWorkflowKey = vi.fn().mockResolvedValue(undefined);
		render(
			<SolutionSetupWizard
				items={[configItem, endpointKey]}
				setupComplete={false}
				onSetConfig={vi.fn()}
				onGenerateWorkflowKey={onGenerateWorkflowKey}
			/>,
		);

		expect(screen.getByText("API_URL")).toBeInTheDocument();
		await userEvent.click(screen.getByRole("button", { name: /next/i }));

		expect(
			screen.getByRole("heading", { name: "Endpoint keys" }),
		).toBeInTheDocument();
		expect(screen.getByText("Ticket Webhook")).toBeInTheDocument();
		await userEvent.click(
			screen.getByRole("button", { name: /generate endpoint key/i }),
		);
		expect(onGenerateWorkflowKey).toHaveBeenCalledWith(
			"22222222-2222-2222-2222-222222222222",
		);
	});
});
