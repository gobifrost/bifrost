import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ServiceActionDialog } from "./ServiceActionDialog";
import type { ServiceListItem } from "@/services/services";

const service = {
	id: "svc-1",
	workflow_name: "telegram_bridge",
} as ServiceListItem;

describe("ServiceActionDialog", () => {
	it("renders the approved stop copy", async () => {
		await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "stop" }}
				actionPending={false}
				actionError={null}
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(screen.getByText("Stop telegram_bridge?")).toBeInTheDocument();
		expect(
			screen.getByText(/shuts down gracefully and desired state becomes stopped/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Stop service" }),
		).toBeInTheDocument();
	});

	it("renders the restart and disable copy", async () => {
		const { unmount } = await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "restart" }}
				actionPending={false}
				actionError={null}
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(
			screen.getByText("Restart telegram_bridge?"),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Restart service" }),
		).toBeInTheDocument();
		unmount();

		await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "disable" }}
				actionPending={false}
				actionError={null}
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(
			screen.getByText("Disable telegram_bridge?"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/will not start again until you re-enable it/i),
		).toBeInTheDocument();
	});

	it("confirms and cancels without closing while pending", async () => {
		const onConfirm = vi.fn();
		const onOpenChange = vi.fn();
		const { user } = await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "restart" }}
				actionPending={false}
				actionError={null}
				onConfirm={onConfirm}
				onOpenChange={onOpenChange}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: "Restart service" }),
		);
		expect(onConfirm).toHaveBeenCalledExactlyOnceWith();
	});

	it("shows pending and error states", async () => {
		const { unmount } = await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "disable" }}
				actionPending={true}
				actionError={null}
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(
			screen.getByRole("button", { name: "Disabling…" }),
		).toBeDisabled();
		expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
		unmount();

		await renderWithProviders(
			<ServiceActionDialog
				pending={{ service, action: "stop" }}
				actionPending={false}
				actionError="Could not stop the service."
				onConfirm={vi.fn()}
				onOpenChange={vi.fn()}
			/>,
		);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Could not stop the service.",
		);
		expect(
			screen.getByRole("button", { name: "Retry stop" }),
		).toBeInTheDocument();
	});
});
