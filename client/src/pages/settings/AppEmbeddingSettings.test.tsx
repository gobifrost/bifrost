import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AppEmbeddingSettings } from "./AppEmbeddingSettings";

const { saveSettings } = vi.hoisted(() => ({ saveSettings: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: () => ({ data: { allowed_origins: [] }, refetch: vi.fn() }),
		useMutation: () => ({ mutateAsync: saveSettings }),
	},
}));

it("saves an exact origin from the admin settings", async () => {
	render(<AppEmbeddingSettings />);
	const user = userEvent.setup();
	await user.type(
		screen.getByLabelText("Allowed Embed Origins"),
		"https://Halo.Example.com",
	);
	await user.click(screen.getByRole("button", { name: "Add Origin" }));
	await user.click(screen.getByRole("button", { name: "Save Changes" }));
	expect(saveSettings).toHaveBeenCalledWith({
		body: { allowed_origins: ["https://halo.example.com"] },
	});
});
