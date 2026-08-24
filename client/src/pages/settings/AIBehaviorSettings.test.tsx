import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn().mockResolvedValue({ default_system_prompt: "Be direct." });
const refetch = vi.fn().mockResolvedValue(undefined);
const behaviorData = { default_system_prompt: "Be helpful." };

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: () => ({
			data: behaviorData,
			isLoading: false,
			refetch,
		}),
		useMutation: () => ({ mutateAsync, isPending: false }),
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AIBehaviorSettings } from "./AIBehaviorSettings";

describe("AIBehaviorSettings", () => {
	it("saves agentless Chat instructions", async () => {
		const user = userEvent.setup();
		render(<AIBehaviorSettings />);

		const instructions = screen.getByLabelText("Instructions");
		await user.clear(instructions);
		await user.type(instructions, "Be direct.");
		await user.click(screen.getByRole("button", { name: "Save instructions" }));

		await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
			body: { default_system_prompt: "Be direct." },
		}));
	});
});
