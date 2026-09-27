import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FailoverProfileField } from "./FailoverProfileField";
import type { AIModelProfile } from "@/services/aiModels";

function profile(overrides: Partial<AIModelProfile>): AIModelProfile {
	return {
		id: "00000000-0000-0000-0000-000000000000",
		name: "Profile",
		connection_id: "conn-a",
		model: "model-a",
		connection: {
			id: "conn-a",
			name: "Conn A",
			provider: "openrouter",
			endpoint: null,
			anthropic_prompt_cache_supported: null,
		},
		assignment_keys: [],
		referenced_agent_count: 0,
		created_at: "2026-01-01T00:00:00Z",
		updated_at: "2026-01-01T00:00:00Z",
		capabilities: null,
		enabled_for_chat: false,
		default_max_tokens: null,
		...overrides,
	} as AIModelProfile;
}

const profiles = [
	profile({ id: "11111111-1111-1111-1111-111111111111", name: "Fast", model: "fast-model", connection_id: "conn-a" }),
	profile({ id: "22222222-2222-2222-2222-222222222222", name: "Balanced", model: "balanced-model", connection_id: "conn-b" }),
];

describe("FailoverProfileField", () => {
	it("excludes the profile being edited from the options", async () => {
		const user = userEvent.setup();
		render(
			<FailoverProfileField
				id="failover"
				profiles={profiles}
				currentProfileId="11111111-1111-1111-1111-111111111111"
				currentConnectionId="conn-a"
				value={null}
				onChange={() => {}}
			/>,
		);
		await user.click(screen.getByLabelText("Failover profile"));
		expect(screen.queryByText("Fast · fast-model")).toBeNull();
		expect(screen.getByText("Balanced · balanced-model")).toBeTruthy();
	});

	it("selecting none clears the fallback", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		render(
			<FailoverProfileField
				id="failover"
				profiles={profiles}
				currentProfileId={null}
				currentConnectionId="conn-a"
				value="22222222-2222-2222-2222-222222222222"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByLabelText("Failover profile"));
		await user.click(screen.getByText("None — fail the run instead"));
		expect(onChange).toHaveBeenCalledWith(null);
	});

	it("warns when the fallback shares the connection", () => {
		render(
			<FailoverProfileField
				id="failover"
				profiles={profiles}
				currentProfileId={null}
				currentConnectionId="conn-a"
				value="11111111-1111-1111-1111-111111111111"
				onChange={() => {}}
			/>,
		);
		expect(screen.getByRole("note").textContent).toMatch(/Same connection/);
	});

	it("stays quiet for a different-connection fallback", () => {
		render(
			<FailoverProfileField
				id="failover"
				profiles={profiles}
				currentProfileId={null}
				currentConnectionId="conn-a"
				value="22222222-2222-2222-2222-222222222222"
				onChange={() => {}}
			/>,
		);
		expect(screen.queryByRole("note")).toBeNull();
	});
});
