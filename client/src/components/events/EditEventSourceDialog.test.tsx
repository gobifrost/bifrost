/**
 * Component tests for EditEventSourceDialog.
 *
 * Covers the schedule-edit path: initial state rehydrates from the passed
 * source, cron validation runs via authFetch, and submit dispatches the
 * update with the edited values. Webhook branch is exercised more lightly
 * (only the name-edit happy path) because the dynamic config form is
 * covered separately.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockUpdate = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div data-marker="org-select" />,
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
	$api: {
		useQuery: vi.fn(() => ({ data: undefined, isLoading: false })),
		useMutation: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
	},
}));

vi.mock("@/services/events", async () => {
	const actual = await vi.importActual<typeof import("@/services/events")>(
		"@/services/events",
	);
	return {
		...actual,
		useUpdateEventSource: () => ({
			mutateAsync: mockUpdate,
			isPending: false,
		}),
		useWebhookAdapters: () => ({
			data: { adapters: [] },
		}),
	};
});

import { EditEventSourceDialog } from "./EditEventSourceDialog";
import type { EventSource } from "@/services/events";

function makeWebhookSource(overrides: Partial<EventSource> = {}): EventSource {
	return {
		id: "src-w1",
		name: "Generic Webhook",
		source_type: "webhook",
		organization_id: null,
		is_active: true,
		webhook: {
			adapter_name: "generic",
			integration_id: null,
			integration_name: null,
			config: {},
			callback_url: "https://example.com/hooks/src-w1",
			external_id: null,
			expires_at: null,
			rate_limit_per_minute: 60,
			rate_limit_window_seconds: 60,
			rate_limit_enabled: true,
		},
		...overrides,
	} as unknown as EventSource;
}

beforeEach(() => {
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue({});
	mockAuthFetch.mockReset();
	mockAuthFetch.mockResolvedValue({
		ok: true,
		json: async () => ({
			valid: true,
			human_readable: "Every day at 9:00 AM",
		}),
	});
});

function makeScheduleSource(
	overrides: Partial<EventSource> = {},
): EventSource {
	return {
		id: "src-1",
		name: "Daily Sync",
		source_type: "schedule",
		organization_id: null,
		is_active: true,
		schedule: {
			cron_expression: "0 9 * * *",
			timezone: "UTC",
			enabled: true,
		},
		...overrides,
	} as unknown as EventSource;
}

describe("EditEventSourceDialog — schedule", () => {
	it("pre-fills the form from the source and submits the update", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<EditEventSourceDialog
				source={makeScheduleSource()}
				open
				onOpenChange={onOpenChange}
			/>,
		);

		// Name is pre-filled
		const nameInput = screen.getByLabelText(/^name$/i) as HTMLInputElement;
		expect(nameInput.value).toBe("Daily Sync");

		// Cron is pre-filled too
		const cronInput = screen.getByLabelText(
			/cron expression/i,
		) as HTMLInputElement;
		expect(cronInput.value).toBe("0 9 * * *");

		// Change the name and submit
		fireEvent.change(nameInput, { target: { value: "Nightly" } });
		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const body = mockUpdate.mock.calls[0]![0].body;
		expect(body.name).toBe("Nightly");
		expect(body.schedule.cron_expression).toBe("0 9 * * *");
		await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
		const validationBody = JSON.parse(
			mockAuthFetch.mock.calls[0]![1].body as string,
		);
		expect(validationBody).toEqual({
			expression: "0 9 * * *",
			timezone: "UTC",
		});
	});

	it("renders the overlap policy select with three options and defaults to skip", async () => {
		const { user } = renderWithProviders(
			<EditEventSourceDialog
				source={makeScheduleSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		const trigger = screen.getByRole("combobox", { name: /overlap policy/i });
		expect(trigger).toBeInTheDocument();
		// Default value shown in trigger
		expect(trigger).toHaveTextContent(/skip/i);

		// Open the dropdown and verify all three options are present
		await user.click(trigger);
		expect(
			await screen.findByRole("option", { name: /^skip$/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("option", { name: /^queue$/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("option", { name: /^replace$/i }),
		).toBeInTheDocument();
	});

	it("includes overlap_policy in the update payload", async () => {
		const { user } = renderWithProviders(
			<EditEventSourceDialog
				source={makeScheduleSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		// Change overlap policy to "queue"
		const trigger = screen.getByRole("combobox", { name: /overlap policy/i });
		await user.click(trigger);
		await user.click(await screen.findByRole("option", { name: /^queue$/i }));

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const body = mockUpdate.mock.calls[0]![0].body;
		expect(body.schedule.overlap_policy).toBe("queue");
	});

	it("does not render rate-limit section for schedule sources", () => {
		renderWithProviders(
			<EditEventSourceDialog
				source={makeScheduleSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		expect(
			screen.queryByLabelText(/rate limit/i),
		).not.toBeInTheDocument();
	});

	it("surfaces a validation error when cron is cleared on submit", async () => {
		const { user } = renderWithProviders(
			<EditEventSourceDialog
				source={makeScheduleSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		fireEvent.change(screen.getByLabelText(/cron expression/i), {
			target: { value: "" },
		});

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		expect(
			await screen.findByText(/cron expression is required/i),
		).toBeInTheDocument();
		expect(mockUpdate).not.toHaveBeenCalled();
	});
});

describe("EditEventSourceDialog — rate-limit hit counter", () => {
	it("renders the rejection count when rate_limited_count_24h > 0", () => {
		renderWithProviders(
			<EditEventSourceDialog
				source={makeWebhookSource({
					webhook: {
						adapter_name: "generic",
						integration_id: null,
						integration_name: null,
						config: {},
						callback_url: "https://example.com/hooks/src-w1",
						external_id: null,
						expires_at: null,
						rate_limit_per_minute: 60,
						rate_limit_window_seconds: 60,
						rate_limit_enabled: true,
						rate_limited_count_24h: 5,
					},
				})}
				open
				onOpenChange={() => {}}
			/>,
		);

		expect(
			screen.getByText(/5 requests rate limited in the last 24 hours/i),
		).toBeInTheDocument();
	});

	it("does NOT render the rejection count when rate_limited_count_24h is 0", () => {
		renderWithProviders(
			<EditEventSourceDialog
				source={makeWebhookSource({
					webhook: {
						adapter_name: "generic",
						integration_id: null,
						integration_name: null,
						config: {},
						callback_url: "https://example.com/hooks/src-w1",
						external_id: null,
						expires_at: null,
						rate_limit_per_minute: 60,
						rate_limit_window_seconds: 60,
						rate_limit_enabled: true,
						rate_limited_count_24h: 0,
					},
				})}
				open
				onOpenChange={() => {}}
			/>,
		);

		expect(
			screen.queryByText(/rate limited in the last 24 hours/i),
		).not.toBeInTheDocument();
	});
});

describe("EditEventSourceDialog — webhook rate-limit", () => {
	it("renders rate-limit section with pre-filled values for webhook sources", () => {
		renderWithProviders(
			<EditEventSourceDialog
				source={makeWebhookSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		const rpmInput = screen.getByLabelText(
			/^max events$/i,
		) as HTMLInputElement;
		expect(rpmInput.value).toBe("60");

		const windowInput = screen.getByLabelText(
			/per \(seconds\)/i,
		) as HTMLInputElement;
		expect(windowInput.value).toBe("60");

		// Enabled switch is present
		expect(screen.getByLabelText(/^enabled$/i)).toBeInTheDocument();
	});

	it("includes rate_limit fields in the update payload", async () => {
		const { user } = renderWithProviders(
			<EditEventSourceDialog
				source={makeWebhookSource()}
				open
				onOpenChange={() => {}}
			/>,
		);

		// Change the per-minute limit
		fireEvent.change(
			screen.getByLabelText(/^max events$/i),
			{ target: { value: "30" } },
		);

		await user.click(screen.getByRole("button", { name: /save changes/i }));

		await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
		const body = mockUpdate.mock.calls[0]![0].body;
		expect(body.webhook.rate_limit_per_minute).toBe(30);
		expect(body.webhook.rate_limit_window_seconds).toBe(60);
		expect(body.webhook.rate_limit_enabled).toBe(true);
	});
});
