import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { NotificationCenter } from "./NotificationCenter";
import { useNotificationStore } from "@/stores/notificationStore";
import type { Notification } from "@/stores/notificationStore";

const state = vi.hoisted(() => ({
	notifications: [] as Notification[],
	dismiss: vi.fn(),
	clearAll: vi.fn(),
	isLoading: false,
	error: null as Error | null,
	refetch: vi.fn(),
	isFetching: false,
}));
vi.mock("@/hooks/useNotifications", () => ({ useNotifications: () => state }));
beforeEach(() => {
	vi.clearAllMocks();
	state.isLoading = false;
	state.error = null;
	state.isFetching = false;
	state.notifications = [];
	useNotificationStore.setState({ notifications: [], alerts: [] });
});

it("distinguishes initial loading from an empty result", async () => {
	state.isLoading = true;
	const user = userEvent.setup();
	const view = render(
		<MemoryRouter>
			<NotificationCenter />
		</MemoryRouter>,
	);
	await user.click(screen.getByRole("button", { name: "Notifications" }));
	expect(screen.getByRole("status")).toHaveTextContent(
		"Loading notifications",
	);
	expect(screen.queryByText("No notifications")).not.toBeInTheDocument();
	state.isLoading = false;
	view.rerender(
		<MemoryRouter>
			<NotificationCenter />
		</MemoryRouter>,
	);
	expect(screen.getByText("No notifications")).toBeVisible();
});

it("retains local messages on fetch failure and offers guarded retry", async () => {
	state.error = new Error("Unavailable");
	useNotificationStore
		.getState()
		.addAlert({
			title: "Saved locally",
			body: "Your existing notification remains readable.",
			status: "info",
		});
	const user = userEvent.setup();
	const view = render(
		<MemoryRouter>
			<NotificationCenter />
		</MemoryRouter>,
	);
	await user.click(screen.getByRole("button", { name: "Notifications" }));
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Couldn't load notifications",
	);
	expect(
		screen.getByRole("article", { name: "Saved locally" }),
	).toBeVisible();
	expect(screen.queryByText("No notifications")).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry" }),
	);
	expect(state.refetch).toHaveBeenCalledOnce();
	state.isFetching = true;
	view.rerender(
		<MemoryRouter>
			<NotificationCenter />
		</MemoryRouter>,
	);
	expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
	await user.click(
		screen.getByRole("button", { name: "Dismiss Saved locally" }),
	);
	expect(
		screen.queryByRole("article", { name: "Saved locally" }),
	).not.toBeInTheDocument();
	expect(screen.queryByText("No notifications")).not.toBeInTheDocument();
});

it("opens explainer details from a notification Learn more button", async () => {
	state.notifications = [
		{
			id: "k8s-1",
			category: "system",
			title: "Kubernetes execution enabled",
			description: "Heavy builds now run in Kubernetes pods.",
			status: "awaiting_action",
			percent: null,
			error: null,
			result: null,
			metadata: {
				action_url: "/settings/kubernetes-executions",
				details: {
					title: "Kubernetes execution is on",
					paragraphs: ["Pods do the heavy builds now."],
					primary_label: "Open execution settings",
				},
			},
			createdAt: new Date().toISOString(),
			updatedAt: new Date().toISOString(),
			userId: "system",
		},
	];
	const user = userEvent.setup();
	render(
		<MemoryRouter>
			<NotificationCenter />
		</MemoryRouter>,
	);
	await user.click(screen.getByRole("button", { name: "Notifications" }));
	await user.click(screen.getByRole("button", { name: "Learn more" }));

	expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
	expect(
		screen.getByText("Pods do the heavy builds now."),
	).toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Open execution settings" }),
	);
	expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});
