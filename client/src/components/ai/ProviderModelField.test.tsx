import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

const { listProviderModels } = vi.hoisted(() => ({
	listProviderModels: vi.fn(),
}));

vi.mock("@/services/aiModels", () => ({ listProviderModels }));
vi.mock("@/components/ui/combobox", () => ({
	Combobox: ({
		id,
		value,
		onValueChange,
		options,
		placeholder,
		disabled,
	}: {
		id: string;
		value: string;
		onValueChange: (value: string) => void;
		options: { value: string; label: string }[];
		placeholder: string;
		disabled: boolean;
	}) => (
		<select
			id={id}
			value={value}
			disabled={disabled}
			onChange={(event) => onValueChange(event.target.value)}
		>
			<option value="">{placeholder}</option>
			{options.map((option) => (
				<option key={option.value} value={option.value}>
					{option.label}
				</option>
			))}
		</select>
	),
}));

import { ProviderModelField } from "./ProviderModelField";

function renderField(connectionId: string, onValueChange = vi.fn()) {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return {
		onValueChange,
		...render(
			<QueryClientProvider client={client}>
				<ProviderModelField
					id="model"
					connectionId={connectionId}
					value=""
					onValueChange={onValueChange}
				/>
			</QueryClientProvider>,
		),
	};
}

describe("ProviderModelField", () => {
	it("stays empty and disabled until a provider is selected", () => {
		renderField("");

		expect(screen.getByLabelText("Model")).toBeDisabled();
		expect(screen.getByRole("option")).toHaveTextContent(
			"Select a provider first",
		);
		expect(listProviderModels).not.toHaveBeenCalled();
	});

	it("loads the selected provider's catalog", async () => {
		listProviderModels.mockResolvedValue({
			provider: "openai",
			models: [
				{
					id: "text-embedding-3-large",
					display_name: "Text Embedding 3 Large",
					output_modalities: ["text"],
				},
			],
		});
		const { onValueChange } = renderField("connection-1");

		await waitFor(() =>
			expect(
				screen.getByRole("option", { name: "Text Embedding 3 Large" }),
			).toBeInTheDocument(),
		);
		fireEvent.change(screen.getByLabelText("Model"), {
			target: { value: "text-embedding-3-large" },
		});
		expect(onValueChange).toHaveBeenCalledWith("text-embedding-3-large");
	});
});
