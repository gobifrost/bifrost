import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test-utils";
import { PricingEditDialog } from "./PricingEditDialog";

beforeEach(() => {
	vi.restoreAllMocks();
	Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
		value: vi.fn(),
		configurable: true,
	});
});

describe("PricingEditDialog", () => {
	it("edits optional cache prices without requiring them", async () => {
		const onChange = vi.fn();
		const onSave = vi.fn();
		renderWithProviders(
			<PricingEditDialog
				open
				editing
				draft={{
					provider: "anthropic",
					model: "Claude Haiku 4.5",
					inputPrice: "1",
					outputPrice: "5",
					cacheReadPrice: "",
					cacheWritePrice: "",
				}}
				pending={false}
				failed={false}
				onChange={onChange}
				onClose={vi.fn()}
				onSave={onSave}
			/>,
		);

		await userEvent.type(screen.getByLabelText("Cache read price (optional)"), "1");
		expect(onChange).toHaveBeenLastCalledWith(
			expect.objectContaining({ cacheReadPrice: "1" }),
		);
		await userEvent.click(screen.getByRole("button", { name: /save/i }));
		expect(onSave).toHaveBeenCalled();
	});

	it("locks the form while pending and focuses persistent errors", async () => {
		const onClose = vi.fn();
		const onSave = vi.fn();
		renderWithProviders(
			<PricingEditDialog
				open
				editing={false}
				draft={{
					provider: "openai",
					model: "gpt-4.1",
					inputPrice: "5.00",
					outputPrice: "15.00",
					cacheReadPrice: "",
					cacheWritePrice: "",
				}}
				pending
				failed
				onChange={vi.fn()}
				onClose={onClose}
				onSave={onSave}
			/>,
		);

		expect(screen.getByLabelText("Provider")).toBeDisabled();
		expect(screen.getByLabelText("Model")).toBeDisabled();
		expect(screen.getByLabelText("Input price")).toBeDisabled();
		expect(screen.getByLabelText("Output price")).toBeDisabled();
		expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled();
		expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled();

		const error = screen.getByRole("alert");
		await waitFor(() => expect(error).toHaveFocus());

		await userEvent.setup().keyboard("{Escape}");
		expect(onClose).not.toHaveBeenCalled();
		expect(onSave).not.toHaveBeenCalled();
	});

	it("submits through the native form and preserves opener focus on close", async () => {
		const user = userEvent.setup();

		function Harness() {
			const [open, setOpen] = useState(false);

			return (
				<>
					<button type="button" onClick={() => setOpen(true)}>
						Open pricing dialog
					</button>
					<PricingEditDialog
						open={open}
						editing={false}
						draft={{
							provider: "openai",
							model: "gpt-4.1",
							inputPrice: "5.00",
							outputPrice: "15.00",
							cacheReadPrice: "",
							cacheWritePrice: "",
						}}
						pending={false}
						failed={false}
						onChange={vi.fn()}
						onClose={() => setOpen(false)}
						onSave={() => setOpen(false)}
					/>
				</>
			);
		}

		renderWithProviders(<Harness />);

		await user.click(screen.getByRole("button", { name: /open pricing dialog/i }));
		await user.click(screen.getByRole("button", { name: /save pricing/i }));

		await waitFor(() => {
			expect(screen.getByRole("button", { name: /open pricing dialog/i })).toHaveFocus();
		});
	});
});
