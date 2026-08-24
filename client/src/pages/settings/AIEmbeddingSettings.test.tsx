import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutateAsync = vi.fn().mockResolvedValue({
	saved: true,
	needs_reindex_confirmation: false,
	notification_id: null,
});
const refetch = vi.fn().mockResolvedValue(undefined);
const connectionsData = [
	{ id: "connection-1", name: "Default", provider: "openai" },
	{ id: "connection-2", name: "Router", provider: "openrouter" },
];
let embeddingData: { connection_id: string; model: string } | undefined;

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (_method: string, path: string) =>
			path.endsWith("connections")
				? { data: connectionsData, isLoading: false }
				: { data: embeddingData, isLoading: false, refetch },
		useMutation: () => ({ mutateAsync, isPending: false }),
	},
}));
vi.mock("@/components/ai/ProviderModelField", () => ({
	ProviderModelField: ({
		id,
		connectionId,
		value,
		onValueChange,
	}: {
		id: string;
		connectionId: string;
		value: string;
		onValueChange: (value: string) => void;
	}) => (
		<label htmlFor={id}>
			Model
			<select
				id={id}
				value={value}
				disabled={!connectionId}
				onChange={(event) => onValueChange(event.target.value)}
			>
				<option value="">Select a model</option>
				<option value="text-embedding-3-small">
					Text Embedding 3 Small
				</option>
				<option value="text-embedding-3-large">
					Text Embedding 3 Large
				</option>
			</select>
		</label>
	),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AIEmbeddingSettings } from "./AIEmbeddingSettings";

describe("AIEmbeddingSettings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		embeddingData = undefined;
	});

	it("does not invent an embedding model before a provider is selected", () => {
		render(<AIEmbeddingSettings />);

		expect(screen.getByLabelText("Model")).toBeDisabled();
		expect(screen.getByLabelText("Model")).toHaveValue("");
		expect(
			screen.getByRole("button", { name: "Save embeddings" }),
		).toBeDisabled();
	});

	it("saves a provider connection and selected embedding model together", async () => {
		embeddingData = {
			connection_id: "connection-1",
			model: "text-embedding-3-small",
		};
		const user = userEvent.setup();
		render(<AIEmbeddingSettings />);

		await user.selectOptions(
			screen.getByLabelText("Model"),
			"text-embedding-3-large",
		);
		await user.click(
			screen.getByRole("button", { name: "Save embeddings" }),
		);

		await waitFor(() =>
			expect(mutateAsync).toHaveBeenCalledWith({
				body: {
					connection_id: "connection-1",
					model: "text-embedding-3-large",
					confirm_reindex: false,
				},
			}),
		);
	});

	it("clears the model when the provider changes", async () => {
		embeddingData = {
			connection_id: "connection-1",
			model: "text-embedding-3-small",
		};
		const user = userEvent.setup();
		render(<AIEmbeddingSettings />);

		await user.click(screen.getByLabelText("Provider connection"));
		await user.click(screen.getByRole("option", { name: /Router/ }));

		expect(screen.getByLabelText("Model")).toHaveValue("");
		expect(
			screen.getByRole("button", { name: "Save embeddings" }),
		).toBeDisabled();
	});
});
