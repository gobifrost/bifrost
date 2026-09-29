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

function renderField(connectionId: string, onValueChange = vi.fn(), value = "") {
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
					value={value}
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
			source: "catalog",
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
		expect(
			screen.getByText(/From the models\.dev catalog, which may list models your account cannot use\./),
		).toBeInTheDocument();
		fireEvent.change(screen.getByLabelText("Model"), {
			target: { value: "text-embedding-3-large" },
		});
		expect(onValueChange).toHaveBeenCalledWith("text-embedding-3-large");
	});
});


it("retries catalog failure without losing a selected model", async () => {
	listProviderModels.mockRejectedValueOnce(new Error("Synthetic failure")).mockResolvedValueOnce({ models: [{ id: "existing-model", display_name: "Existing model" }] });
	renderField("retry-connection", vi.fn(), "existing-model");
	expect(await screen.findByRole("alert")).toHaveTextContent("selected model is preserved");
	expect(screen.getByRole("textbox", { name: "Model" })).toHaveValue("existing-model");
	fireEvent.click(screen.getByRole("button", { name: "Retry model list" }));
	await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
	expect(screen.getByRole("combobox", { name: "Model" })).toHaveValue("existing-model");
});

it("allows editing an existing model ID when the catalog is empty", async () => {
	listProviderModels.mockResolvedValueOnce({ models: [] });
	const onValueChange = vi.fn();
	renderField("empty-connection", onValueChange, "existing-model");
	const input = await screen.findByRole("textbox", { name: "Model" });
	fireEvent.change(input, { target: { value: "replacement-model" } });
	expect(onValueChange).toHaveBeenCalledWith("replacement-model");
});

it("lets an administrator type a model the catalog does not list", async () => {
	listProviderModels.mockResolvedValueOnce({
		source: "catalog",
		models: [{ id: "listed", display_name: "Listed" }],
	});
	const onValueChange = vi.fn();
	renderField("typed-connection", onValueChange);

	fireEvent.click(await screen.findByRole("button", { name: "Enter a model ID instead" }));
	fireEvent.change(screen.getByRole("textbox", { name: "Model" }), {
		target: { value: "my-finetune" },
	});
	expect(onValueChange).toHaveBeenCalledWith("my-finetune");
	fireEvent.click(screen.getByRole("button", { name: "Choose from the model list" }));
	expect(screen.getByRole("combobox", { name: "Model" })).toBeInTheDocument();
});
