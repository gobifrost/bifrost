import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";

import { RegistrationLinkDialog } from "./RegistrationLinkDialog";

let originalWriteText: typeof navigator.clipboard.writeText | undefined;

beforeEach(() => {
	originalWriteText = navigator.clipboard?.writeText.bind(
		navigator.clipboard,
	);
});

afterEach(() => {
	if (originalWriteText && navigator.clipboard) {
		Object.defineProperty(navigator.clipboard, "writeText", {
			configurable: true,
			value: originalWriteText,
		});
	}
});

describe("RegistrationLinkDialog", () => {
	it("shows a centered success message with send and copy actions", () => {
		const onSendEmail = vi.fn();

		renderWithProviders(
			<RegistrationLinkDialog
				open={true}
				email="alice@example.com"
				url="/accept-invite?token=abc"
				canSendEmail={true}
				onSendEmail={onSendEmail}
				onOpenChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: /user created/i }),
		).toBeInTheDocument();
		expect(
			screen.getByText(
				/send this to alice@example.com so they can finish logging in/i,
			),
		).toBeInTheDocument();
		expect(
			screen.queryByText(
				`${window.location.origin}/accept-invite?token=abc`,
			),
		).not.toBeInTheDocument();
		expect(screen.queryByText("Recipient")).not.toBeInTheDocument();
		expect(screen.queryByText("Destination")).not.toBeInTheDocument();
		expect(screen.queryByText(/link host/i)).not.toBeInTheDocument();
		expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /send registration email/i }),
		).toBeEnabled();
		expect(
			screen.getByRole("button", { name: /copy registration link/i }),
		).toBeInTheDocument();
	});

	it("keeps clipboard failures inside the button state without throwing", () => {
		(
			navigator.clipboard as unknown as {
				writeText: undefined;
			}
		).writeText = undefined;

		renderWithProviders(
			<RegistrationLinkDialog
				open={true}
				url="https://example.test/accept-invite?token=abc"
				canSendEmail={true}
				onOpenChange={vi.fn()}
			/>,
		);

		fireEvent.click(
			screen.getByRole("button", { name: /copy registration link/i }),
		);

		expect(
			screen.getByRole("button", { name: /copy registration link/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /copied/i }),
		).not.toBeInTheDocument();
	});

	it("animates the copy button to a success state", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator.clipboard, "writeText", {
			configurable: true,
			value: writeText,
		});

		const { user } = renderWithProviders(
			<RegistrationLinkDialog
				open={true}
				url="https://example.test/accept-invite?token=abc"
				canSendEmail={true}
				onOpenChange={vi.fn()}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /copy registration link/i }),
		);

		await waitFor(() => {
			expect(writeText).toHaveBeenCalledWith(
				"https://example.test/accept-invite?token=abc",
			);
			expect(
				screen.getByRole("button", { name: /copied/i }),
			).toBeInTheDocument();
		});
	});

	it("disables sending when invite automation is not configured", () => {
		renderWithProviders(
			<RegistrationLinkDialog
				open={true}
				url="https://example.test/accept-invite?token=abc"
				canSendEmail={false}
				onOpenChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("button", { name: /send registration email/i }),
		).toBeDisabled();
		expect(
			screen.getByLabelText(/registration email automation setup/i),
		).toBeInTheDocument();
	});

	it("calls the send email action", async () => {
		const onSendEmail = vi.fn().mockResolvedValue(undefined);

		const { user } = renderWithProviders(
			<RegistrationLinkDialog
				open={true}
				url="https://example.test/accept-invite?token=abc"
				canSendEmail={true}
				onSendEmail={onSendEmail}
				onOpenChange={vi.fn()}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /send registration email/i }),
		);

		await waitFor(() => expect(onSendEmail).toHaveBeenCalledOnce());
	});
});
