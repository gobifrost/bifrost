import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn().mockResolvedValue({
	saved: true,
	needs_reindex_confirmation: false,
	notification_id: null,
});
const refetch = vi.fn().mockResolvedValue(undefined);
const connectionsData = [{ id: "connection-1", name: "Default", provider: "openai" }];
const embeddingData = { connection_id: "connection-1", model: "text-embedding-3-small" };

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (_method: string, path: string) => path.endsWith("connections")
			? { data: connectionsData, isLoading: false }
			: { data: embeddingData, isLoading: false, refetch },
		useMutation: () => ({ mutateAsync, isPending: false }),
	},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AIEmbeddingSettings } from "./AIEmbeddingSettings";

describe("AIEmbeddingSettings", () => {
	it("saves a provider connection and embedding model together", async () => {
		const user = userEvent.setup();
		render(<AIEmbeddingSettings />);

		const model = screen.getByLabelText("Model");
		await user.clear(model);
		await user.type(model, "text-embedding-3-large");
		await user.click(screen.getByRole("button", { name: "Save embeddings" }));

		await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
			body: {
				connection_id: "connection-1",
				model: "text-embedding-3-large",
				confirm_reindex: false,
			},
		}));
	});
});
