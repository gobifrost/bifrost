import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SolutionSetupChecklist } from "./SolutionSetupChecklist";
import type { SolutionSetupItem } from "@/services/solutions";

const requiredUnset: SolutionSetupItem = {
	key: "api_key",
	type: "secret",
	required: true,
	is_set: false,
	kind: "config",
	has_oauth: false,
	connected: false,
};

const optionalSet: SolutionSetupItem = {
	key: "timeout",
	type: "int",
	required: false,
	is_set: true,
	kind: "config",
	has_oauth: false,
	connected: false,
};

describe("SolutionSetupChecklist", () => {
	it("lists required-unset configs and shows a Set control", () => {
		render(
			<SolutionSetupChecklist
				items={[requiredUnset]}
				setupComplete={false}
				onSet={() => {}}
			/>,
		);
		expect(screen.getByText("api_key")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /set/i })).toBeInTheDocument();
	});

	it("uses a password input for secret-type configs", () => {
		render(
			<SolutionSetupChecklist
				items={[requiredUnset]}
				setupComplete={false}
				onSet={() => {}}
			/>,
		);
		expect(screen.getByTestId("config-value-input-api_key")).toHaveAttribute(
			"type",
			"password",
		);
	});

	it("shows a check icon and no Set button for already-set configs", () => {
		render(
			<SolutionSetupChecklist
				items={[optionalSet]}
				setupComplete={false}
				onSet={() => {}}
			/>,
		);
		expect(screen.getByText("timeout")).toBeInTheDocument();
		// A "Set" button only appears when value entered; the input exists but button isn't visible yet
		expect(screen.queryByRole("button", { name: /^set$/i })).not.toBeInTheDocument();
	});

	it("calls onSet with the key and entered value", async () => {
		const onSet = vi.fn().mockResolvedValue(undefined);
		render(
			<SolutionSetupChecklist
				items={[requiredUnset]}
				setupComplete={false}
				onSet={onSet}
			/>,
		);
		const input = screen.getByTestId("config-value-input-api_key");
		await userEvent.type(input, "mysecret");
		await userEvent.click(screen.getByRole("button", { name: /set/i }));
		expect(onSet).toHaveBeenCalledWith("api_key", "mysecret");
	});

	it("shows a completed banner when setupComplete is true", () => {
		render(
			<SolutionSetupChecklist
				items={[{ ...requiredUnset, is_set: true }]}
				setupComplete={true}
				onSet={() => {}}
			/>,
		);
		expect(screen.getByTestId("setup-complete-banner")).toBeInTheDocument();
	});

	it("shows the default value as placeholder hint when not set", () => {
		render(
			<SolutionSetupChecklist
				items={[{ key: "region", type: "string", required: false, is_set: false, default: "us-east-1", kind: "config", has_oauth: false, connected: false }]}
				setupComplete={false}
				onSet={() => {}}
			/>,
		);
		expect(screen.getByPlaceholderText(/us-east-1/)).toBeInTheDocument();
	});
});
