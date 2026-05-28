/**
 * Component tests for CreateEventSourceDialog.
 *
 * Covers the two source-type branches — webhook and schedule — plus the
 * required-field validation path. The auth context, event/integration
 * hooks, and authFetch (used for cron validation) are all mocked.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";

const mockCreate = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div data-marker="org-select" />,
}));

vi.mock("react-syntax-highlighter", () => ({
	Prism: ({
		children,
	}: {
		children: string;
		language?: string;
		style?: unknown;
		customStyle?: unknown;
		codeTagProps?: unknown;
	}) => <pre>{children}</pre>,
}));

vi.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({
	oneDark: {},
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
	$api: {
		useQuery: vi.fn(() => ({ data: undefined, isLoading: false })),
		useMutation: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
	},
}));

vi.mock("@/services/events", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/events")>(
			"@/services/events",
		);
	return {
		...actual,
		useCreateEventSource: () => ({
			mutateAsync: mockCreate,
			isPending: false,
		}),
		useWebhookAdapters: () => ({
			data: {
				adapters: [
					{
						name: "generic",
						display_name: "Generic Webhook",
						description: "Generic webhook adapter",
						config_schema: {},
					},
				],
			},
		}),
		useTopics: () => ({
			data: {
				curated: [
					{
						topic: "user.invited",
						description: "Fired when a user is invited.",
						category: "Users",
						emitted_by: "Bifrost platform",
						example_body: {
							schema_version: 1,
							occurred_at: "2026-05-28T12:34:56Z",
							organization: { id: "org-1", name: "Acme" },
							actor: {
								type: "user",
								id: "user-1",
								email: "admin@example.com",
								name: "Admin",
							},
							user: {
								id: "user-2",
								email: "new@example.com",
								name: "New User",
							},
						},
					},
				],
				in_use: ["user.invited"],
			},
		}),
	};
});

vi.mock("@/services/integrations", async () => {
	const actual = await vi.importActual<
		typeof import("@/services/integrations")
	>("@/services/integrations");
	return {
		...actual,
		useIntegrations: () => ({ data: { items: [] } }),
	};
});

import { CreateEventSourceDialog } from "./CreateEventSourceDialog";

beforeEach(() => {
	mockCreate.mockReset();
	mockCreate.mockResolvedValue({});
	mockAuthFetch.mockReset();
	mockAuthFetch.mockResolvedValue({
		ok: true,
		json: async () => ({
			valid: true,
			human_readable: "Every day at 9:00 AM",
			next_runs: [],
		}),
	});
});

describe("CreateEventSourceDialog — validation", () => {
	it("shows errors when the form is submitted with missing fields", async () => {
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		await user.click(
			screen.getByRole("button", { name: /create event source/i }),
		);

		// Alert role should surface the validation errors
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent(/name is required/i);
		expect(alert).toHaveTextContent(/webhook adapter/i);
		expect(mockCreate).not.toHaveBeenCalled();
	});
});

describe("CreateEventSourceDialog — webhook happy path", () => {
	it("dispatches createMutation with webhook payload", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={onOpenChange} />,
		);

		fireEvent.change(screen.getByLabelText(/^name$/i), {
			target: { value: "GitHub Hooks" },
		});

		// Select the adapter
		await user.click(
			screen.getByRole("combobox", { name: /webhook adapter/i }),
		);
		await user.click(
			screen.getByRole("option", { name: /generic webhook/i }),
		);

		await user.click(
			screen.getByRole("button", { name: /create event source/i }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		const body = mockCreate.mock.calls[0]![0].body;
		expect(body.name).toBe("GitHub Hooks");
		expect(body.source_type).toBe("webhook");
		expect(body.webhook.adapter_name).toBe("generic");
		// Rate-limit defaults are included
		expect(body.webhook.rate_limit_per_minute).toBe(60);
		expect(body.webhook.rate_limit_window_seconds).toBe(60);
		expect(body.webhook.rate_limit_enabled).toBe(true);
	});
});

describe("CreateEventSourceDialog — webhook rate-limit section", () => {
	it("renders rate-limit inputs for webhook sources", () => {
		renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		expect(screen.getByLabelText(/^max events$/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/per \(seconds\)/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/^enabled$/i)).toBeInTheDocument();
	});

	it("clears rate_limit_per_minute to empty string when field is cleared", () => {
		renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		const input = screen.getByLabelText(
			/^max events$/i,
		) as HTMLInputElement;
		expect(input.value).toBe("60");

		fireEvent.change(input, { target: { value: "" } });
		expect(input.value).toBe("");
	});
});

describe("CreateEventSourceDialog — schedule branch", () => {
	it("reveals cron + timezone inputs and validates the cron expression", async () => {
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		// Switch to schedule
		await user.click(
			screen.getByRole("combobox", { name: /source type/i }),
		);
		await user.click(screen.getByRole("option", { name: /schedule/i }));

		expect(screen.getByLabelText(/cron expression/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/timezone/i)).toBeInTheDocument();

		fireEvent.change(screen.getByLabelText(/cron expression/i), {
			target: { value: "0 9 * * *" },
		});

		await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
		expect(
			await screen.findByText(/every day at 9:00 am/i),
		).toBeInTheDocument();
	});
});

describe("CreateEventSourceDialog — topic branch", () => {
	it("reveals topic picker when source type is changed to topic", async () => {
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		await user.click(
			screen.getByRole("combobox", { name: /source type/i }),
		);
		await user.click(screen.getByRole("option", { name: /^topic$/i }));

		expect(
			screen.getByRole("combobox", { name: /topic/i }),
		).toBeInTheDocument();
		// Webhook / schedule sections should not be visible
		expect(
			screen.queryByLabelText(/webhook adapter/i),
		).not.toBeInTheDocument();
		expect(
			screen.queryByLabelText(/cron expression/i),
		).not.toBeInTheDocument();
	});

	it("opens event source reference help from the dialog header", async () => {
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		await user.click(
			screen.getByRole("button", { name: /event source reference/i }),
		);

		expect(
			await screen.findByText(/python workflow access/i),
		).toBeVisible();
		expect(screen.getAllByText(/from bifrost import workflow, context/i).length).toBeGreaterThan(
			0,
		);
		expect(screen.getByText(/@workflow\(name="handle_builtin_event"\)/i)).toBeVisible();
		expect(screen.getByText(/user\.invited body/i)).toBeVisible();
		expect(screen.getAllByText(/schema_version/i).length).toBeGreaterThan(
			0,
		);
	});

	it("submits with source_type topic and event_type from registry", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={onOpenChange} />,
		);

		// Switch to topic type
		await user.click(
			screen.getByRole("combobox", { name: /source type/i }),
		);
		await user.click(screen.getByRole("option", { name: /^topic$/i }));

		// Pick from the registry
		await user.click(screen.getByRole("combobox", { name: /topic/i }));
		await user.click(
			screen.getByRole("option", { name: /user\.invited/i }),
		);

		await user.click(
			screen.getByRole("button", { name: /create event source/i }),
		);

		await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
		const body = mockCreate.mock.calls[0]![0].body;
		expect(body.source_type).toBe("topic");
		expect(body.event_type).toBe("user.invited");
		// Name should be auto-derived from topic
		expect(body.name).toBe("User Invited");
	});

	it("shows validation error for invalid custom topic", async () => {
		const { user } = renderWithProviders(
			<CreateEventSourceDialog open onOpenChange={() => {}} />,
		);

		await user.click(
			screen.getByRole("combobox", { name: /source type/i }),
		);
		await user.click(screen.getByRole("option", { name: /^topic$/i }));

		// Choose "Custom topic..."
		await user.click(screen.getByRole("combobox", { name: /topic/i }));
		await user.click(screen.getByRole("option", { name: /custom topic/i }));

		// Type an invalid topic (no dot)
		fireEvent.change(screen.getByLabelText(/custom topic/i), {
			target: { value: "nodot" },
		});

		await user.click(
			screen.getByRole("button", { name: /create event source/i }),
		);

		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent(/dot/i);
		expect(mockCreate).not.toHaveBeenCalled();
	});
});
