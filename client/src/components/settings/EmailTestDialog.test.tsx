import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmailTestDialog } from "./EmailTestDialog";

describe("EmailTestDialog", () => {
	it("prefills the recipient with the current user's email", () => {
		render(
			<EmailTestDialog
				open
				onOpenChange={() => {}}
				currentUserEmail="me@example.com"
				onTest={vi.fn()}
				isPending={false}
			/>,
		);
		const input = screen.getByLabelText(/recipient/i) as HTMLInputElement;
		expect(input.value).toBe("me@example.com");
	});

	it("calls onTest with the entered recipient", async () => {
		const onTest = vi.fn();
		render(
			<EmailTestDialog
				open
				onOpenChange={() => {}}
				currentUserEmail="me@example.com"
				onTest={onTest}
				isPending={false}
			/>,
		);
		const input = screen.getByLabelText(/recipient/i);
		await userEvent.clear(input);
		await userEvent.type(input, "other@example.com");
		await userEvent.click(
			screen.getByRole("button", { name: /send test/i }),
		);
		await waitFor(() =>
			expect(onTest).toHaveBeenCalledWith("other@example.com"),
		);
	});

	it("disables the Send button while pending", () => {
		render(
			<EmailTestDialog
				open
				onOpenChange={() => {}}
				currentUserEmail="me@example.com"
				onTest={vi.fn()}
				isPending
			/>,
		);
		const btn = screen.getByRole("button", { name: /sending/i });
		expect(btn).toBeDisabled();
	});
});
