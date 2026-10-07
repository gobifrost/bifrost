/**
 * Component tests for FormRenderer.
 *
 * FormRenderer is the runtime surface: it builds a zod schema from the form
 * definition, wires react-hook-form, applies visibility expressions, renders
 * the right control per field type, and submits to a mutation.
 *
 * We mock:
 * - useSubmitForm — the submit mutation
 * - useLaunchWorkflow — launch workflow side-effect (noop)
 * - JsxTemplateRenderer, FileUploadField, framer-motion — keep DOM simple
 * - react-router-dom useNavigate — to assert navigation targets
 * - sonner toast — to assert the scheduled-success toast
 *
 * Tests cover:
 * - required validation keeps the Submit button disabled
 * - filling a required email field enables Submit and a successful submit
 *   calls the mutation with the right body
 * - email validation surfaces an error for invalid input
 * - visibility_expression hides a field when the condition is false
 * - markdown field renders its content
 * - run-now submit sends no scheduled_at/delay_seconds
 * - scheduled submit sends delay_seconds and navigates to /history with toast
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor, fireEvent } from "@/test-utils";
import type { FormField } from "@/lib/client-types";

// Mock the submit mutation. Each test can override mutateAsync/isPending.
const mockMutateAsync = vi.fn();
vi.mock("@/hooks/useForms", () => ({
	useSubmitForm: () => ({
		mutateAsync: mockMutateAsync,
		isPending: false,
	}),
}));

// Navigation mock — FormRenderer navigates to /history/{id} on run-now
// and /history on scheduled submits.
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
	};
});

// sonner toast — we assert on the scheduled-success toast.
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => mockToastSuccess(...args),
		error: (...args: unknown[]) => mockToastError(...args),
	},
}));

// Launch workflow side-effect: do nothing.
vi.mock("@/hooks/useLaunchWorkflow", () => ({
	useLaunchWorkflow: () => undefined,
}));

// Framer-motion: stub to plain divs so enter/exit animations don't delay the
// test. We strip animation-only props (initial, animate, exit, transition,
// style) so React doesn't warn and the DOM stays clean.
vi.mock("framer-motion", () => {
	const passthrough = ({
		children,
		// strip motion-only props
		initial: _i,
		animate: _a,
		exit: _e,
		transition: _t,
		...rest
	}: Record<string, unknown> & { children?: React.ReactNode }) => (
		<div {...(rest as Record<string, unknown>)}>{children}</div>
	);
	return {
		motion: new Proxy({}, { get: () => passthrough }),
		useReducedMotion: () => true,
		AnimatePresence: ({ children }: { children: React.ReactNode }) => (
			<>{children}</>
		),
	};
});

// FileUploadField — pulled in by default-render; replace with null so it
// doesn't exercise the uploader when we don't need it.
vi.mock("@/components/forms/FileUploadField", () => ({
	FileUploadField: () => <div data-marker="file-upload" />,
}));

// JsxTemplateRenderer — stub to a div.
vi.mock("@/components/ui/jsx-template-renderer", () => ({
	JsxTemplateRenderer: ({ template }: { template: string }) => (
		<div>{template}</div>
	),
}));

// FormContextPanel — stub so dev-mode drawer doesn't require the context.
vi.mock("@/components/forms/FormContextPanel", () => ({
	FormContextPanel: () => <div />,
}));

const mockGetOptions = vi.hoisted(() => vi.fn());
vi.mock("@/services/dataProviders", () => ({
	getFormFieldOptions: mockGetOptions,
}));

vi.mock("@/components/forms/FormCaptcha", () => ({
	FormCaptcha: ({
		onPayloadChange,
	}: {
		onPayloadChange: (payload: string) => void;
	}) => (
		<button type="button" onClick={() => onPayloadChange("captcha-proof")}>
			Verify visitor
		</button>
	),
}));

import { FormRenderer } from "./FormRenderer";

type Form = Parameters<typeof FormRenderer>[0]["form"];

function makeForm(fields: FormField[]): Form {
	return {
		id: "form-1",
		name: "Test Form",
		description: null,
		workflow_id: "wf-1",
		launch_workflow_id: null,
		default_launch_params: {},
		form_schema: { fields },
		access_level: "authenticated",
		organization_id: null,
		created_at: "2026-04-20T00:00:00Z",
		updated_at: "2026-04-20T00:00:00Z",
	} as unknown as Form;
}

beforeEach(() => {
	mockMutateAsync.mockReset();
	mockGetOptions.mockReset().mockResolvedValue([]);
	mockNavigate.mockReset();
	mockToastSuccess.mockReset();
	mockToastError.mockReset();
	mockMutateAsync.mockResolvedValue({
		execution_id: "exec-1",
		status: "Pending",
	});
});

describe("FormRenderer — required validation", () => {
	it("keeps the Submit button disabled when a required field is empty", () => {
		const form = makeForm([
			{ name: "email", label: "Email", type: "email", required: true },
		]);
		renderWithProviders(<FormRenderer form={form} />);

		expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled();
	});

	it("replaces an embedded form with its Markdown confirmation", async () => {
		mockMutateAsync.mockResolvedValueOnce({
			mode: "confirmation",
			status: "accepted",
			confirmation_markdown: "## Thank you\n\nWe received it.",
		});
		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(
			<FormRenderer form={form} preventNavigation />,
		);
		fireEvent.change(screen.getByLabelText(/comment/i), {
			target: { value: "hello" },
		});
		await user.click(screen.getByRole("button", { name: /submit/i }));

		expect(
			await screen.findByRole("heading", { name: "Thank you" }),
		).toBeInTheDocument();
		expect(screen.getByText("We received it.")).toBeInTheDocument();
		expect(mockNavigate).not.toHaveBeenCalled();
		expect(
			screen.queryByRole("button", { name: /submit/i }),
		).not.toBeInTheDocument();
	});

	it("can navigate to a result without exposing scheduling controls", async () => {
		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(
			<FormRenderer
				form={form}
				preventNavigation={false}
				allowScheduling={false}
			/>,
		);

		expect(
			screen.queryByRole("switch", { name: /schedule for later/i }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /submit/i }));

		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith(
				"/history/exec-1",
				expect.any(Object),
			);
		});
	});

	it("submits with the typed value and calls the mutation with the right payload", async () => {
		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(<FormRenderer form={form} />);

		// react-hook-form registers its onChange via native DOM events;
		// userEvent.type in happy-dom doesn't always trigger that listener
		// reliably when the input is uncontrolled, so use fireEvent.change
		// which is what react-hook-form listens to for "change" mode.
		const input = screen.getByLabelText(/comment/i);
		fireEvent.change(input, { target: { value: "hello world" } });

		const submit = screen.getByRole("button", { name: /submit/i });
		await waitFor(() => expect(submit).toBeEnabled(), { timeout: 3000 });

		await user.click(submit);

		await waitFor(() => {
			expect(mockMutateAsync).toHaveBeenCalledTimes(1);
		});
		const body = mockMutateAsync.mock.calls[0]![0];
		expect(body.params.path.form_id).toBe("form-1");
		expect(body.body.form_data.comment).toBe("hello world");
		expect(body.body.submission_nonce).toMatch(/^[A-Za-z0-9-]{16,}$/);
	});

	it("requires and submits a CAPTCHA payload for an anonymous runtime", async () => {
		const form = {
			...makeForm([]),
			captcha_required: true,
		} as Form;
		const { user } = renderWithProviders(
			<FormRenderer form={form} preventNavigation />,
		);

		const submit = screen.getByRole("button", { name: /submit/i });
		expect(submit).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Verify visitor" }),
		);
		await waitFor(() => expect(submit).toBeEnabled());
		await user.click(submit);

		await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
		expect(mockMutateAsync.mock.calls[0]![0].body.captcha_payload).toBe(
			"captcha-proof",
		);
	});

	it("surfaces an 'Invalid email' error for a malformed email on change", async () => {
		const form = makeForm([
			{ name: "email", label: "Email", type: "email", required: true },
		]);
		renderWithProviders(<FormRenderer form={form} />);

		const input = screen.getByLabelText(/email/i);
		fireEvent.change(input, { target: { value: "not-an-email" } });

		expect(
			await screen.findByText(/invalid email address/i, undefined, {
				timeout: 3000,
			}),
		).toBeInTheDocument();
	});
});

describe("FormRenderer — conditional rendering", () => {
	it("hides a field whose visibility_expression evaluates to false", () => {
		const form = makeForm([
			{ name: "age", label: "Age", type: "number", required: false },
			{
				name: "license",
				label: "License Number",
				type: "text",
				required: false,
				// Only visible when age >= 18.
				visibility_expression: "context.field.age >= 18",
			},
		]);
		renderWithProviders(<FormRenderer form={form} />);

		// Age starts empty so license is hidden.
		expect(screen.getByLabelText(/^age$/i)).toBeInTheDocument();
		expect(
			screen.queryByLabelText(/license number/i),
		).not.toBeInTheDocument();
	});
});

describe("FormRenderer — field types", () => {
	it("renders a markdown field's content", () => {
		const form = makeForm([
			{
				name: "intro",
				label: "Intro",
				type: "markdown",
				required: false,
				content: "# Welcome\n\nPlease fill out the form.",
			},
		]);
		renderWithProviders(<FormRenderer form={form} />);

		expect(screen.getByText("Welcome")).toBeInTheDocument();
		expect(
			screen.getByText(/please fill out the form/i),
		).toBeInTheDocument();
	});

	it("renders a textarea for type=textarea", () => {
		const form = makeForm([
			{
				name: "bio",
				label: "Bio",
				type: "textarea",
				required: false,
				placeholder: "Tell us",
			},
		]);
		renderWithProviders(<FormRenderer form={form} />);

		const textarea = screen.getByPlaceholderText("Tell us");
		expect(textarea.tagName).toBe("TEXTAREA");
	});

	it.each([
		[false, "Choose a site"],
		[true, "Contoso HQ"],
	])(
		"re-choosing the selected option of a select (required: %s) leaves %s",
		async (required, shown) => {
			const form = makeForm([
				{
					name: "site",
					label: "Site",
					type: "select",
					required,
					placeholder: "Choose a site",
					options: [{ value: "hq", label: "Contoso HQ" }],
				},
			]);
			const { user } = renderWithProviders(<FormRenderer form={form} />);

			await user.click(screen.getByRole("combobox"));
			await user.click(screen.getByRole("option", { name: "Contoso HQ" }));
			await user.click(screen.getByRole("combobox"));
			await user.click(screen.getByRole("option", { name: "Contoso HQ" }));

			expect(screen.getByRole("combobox")).toHaveTextContent(shown);
		},
	);
});

describe("FormRenderer — scheduling", () => {
	it("submits a body without scheduled_at or delay_seconds when the schedule switch is untouched", async () => {
		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(<FormRenderer form={form} />);

		fireEvent.change(screen.getByLabelText(/comment/i), {
			target: { value: "hi" },
		});

		const submit = screen.getByRole("button", { name: /submit/i });
		await waitFor(() => expect(submit).toBeEnabled(), { timeout: 3000 });

		await user.click(submit);

		await waitFor(() => {
			expect(mockMutateAsync).toHaveBeenCalledTimes(1);
		});

		const body = mockMutateAsync.mock.calls[0]![0].body as Record<
			string,
			unknown
		>;
		expect(body).not.toHaveProperty("scheduled_at");
		expect(body).not.toHaveProperty("delay_seconds");
		expect(body).toMatchObject({
			form_data: { comment: "hi" },
		});

		// Run-now: navigates to /history/{execution_id}.
		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith(
				"/history/exec-1",
				expect.any(Object),
			);
		});
	}, 15000);

	it("sends delay_seconds: 900 when the user picks 'In 15 min' and submits", async () => {
		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(<FormRenderer form={form} />);

		fireEvent.change(screen.getByLabelText(/comment/i), {
			target: { value: "hi" },
		});

		const submit = screen.getByRole("button", { name: /submit/i });
		await waitFor(() => expect(submit).toBeEnabled(), { timeout: 3000 });

		// Flip "Schedule for later" and pick "In 15 min".
		await user.click(
			screen.getByRole("switch", { name: /schedule for later/i }),
		);
		await user.click(screen.getByRole("button", { name: /in 15 min/i }));

		await user.click(submit);

		await waitFor(() => {
			expect(mockMutateAsync).toHaveBeenCalledTimes(1);
		});

		const body = mockMutateAsync.mock.calls[0]![0].body as Record<
			string,
			unknown
		>;
		expect(body.delay_seconds).toBe(900);
		expect(body).not.toHaveProperty("scheduled_at");
	}, 15000);

	it("navigates to /history and toasts the scheduled time on a Scheduled response", async () => {
		mockMutateAsync.mockResolvedValueOnce({
			execution_id: "exec-sched",
			status: "Scheduled",
			scheduled_at: "2026-05-01T12:00:00Z",
		});

		const form = makeForm([
			{
				name: "comment",
				label: "Comment",
				type: "text",
				required: false,
			},
		]);
		const { user } = renderWithProviders(<FormRenderer form={form} />);

		fireEvent.change(screen.getByLabelText(/comment/i), {
			target: { value: "hi" },
		});

		const submit = screen.getByRole("button", { name: /submit/i });
		await waitFor(() => expect(submit).toBeEnabled(), { timeout: 3000 });

		await user.click(
			screen.getByRole("switch", { name: /schedule for later/i }),
		);
		await user.click(screen.getByRole("button", { name: /in 15 min/i }));

		await user.click(submit);

		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith("/history");
		});

		expect(mockToastSuccess).toHaveBeenCalledTimes(1);
		const toastMsg = mockToastSuccess.mock.calls[0]![0] as string;
		expect(toastMsg).toMatch(/scheduled for/i);
	}, 15000);
});

describe("submission recovery", () => {
	it("protects pending answers, rejects duplicate submits and retains answers for retry", async () => {
		let rejectSubmission!: (error: Error) => void;
		mockMutateAsync.mockImplementationOnce(
			() =>
				new Promise((_, reject) => {
					rejectSubmission = reject;
				}),
		);
		const { user } = renderWithProviders(
			<FormRenderer
				form={makeForm([
					{
						name: "comment",
						label: "Comment",
						type: "text",
						required: true,
					},
				])}
			/>,
		);
		const input = screen.getByLabelText(/comment/i);
		await user.type(input, "Keep this answer");
		const submit = screen.getByRole("button", { name: "Submit" });
		await waitFor(() => expect(submit).toBeEnabled());
		await user.click(submit);
		await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(1));
		expect(input).toBeDisabled();
		fireEvent.submit(input.closest("form")!);
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(mockMutateAsync).toHaveBeenCalledTimes(1);
		rejectSubmission(new Error("Service temporarily unavailable"));
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("Service temporarily unavailable");
		await waitFor(() => expect(alert).toHaveFocus());
		expect(input).toBeEnabled();
		expect(input).toHaveValue("Keep this answer");
		await user.click(screen.getByRole("button", { name: "Submit" }));
		await waitFor(() => expect(mockMutateAsync).toHaveBeenCalledTimes(2));
		expect(mockMutateAsync.mock.calls[1][0].body.form_data.comment).toBe(
			"Keep this answer",
		);
	});
});

it("retries failed choices without clearing other answers and protects the pending retry", async () => {
	let resolveOptions!: (value: unknown) => void;
	mockGetOptions.mockRejectedValue(new Error("Unavailable"));
	const { user } = renderWithProviders(
		<FormRenderer
			form={makeForm([
				{
					name: "summary",
					label: "Summary",
					type: "text",
					required: true,
				},
				{
					name: "owner",
					label: "Owner",
					type: "select",
					required: true,
					has_dynamic_options: true,
				},
			])}
		/>,
	);
	await screen.findByRole("button", { name: "Retry choices" });
	await user.type(screen.getByLabelText(/Summary/), "Keep this answer");
	mockGetOptions.mockImplementation(
		() =>
			new Promise((resolve) => {
				resolveOptions = resolve;
			}),
	);
	await user.click(screen.getByRole("button", { name: "Retry choices" }));
	await waitFor(() =>
		expect(
			screen.getByRole("button", { name: "Retrying…" }),
		).toBeDisabled(),
	);
	expect(screen.getByLabelText(/Summary/)).toHaveValue("Keep this answer");
	resolveOptions([{ value: "support", label: "Support" }]);
	await waitFor(() =>
		expect(
			screen.queryByRole("button", { name: "Retrying…" }),
		).not.toBeInTheDocument(),
	);
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "Support" }));
	expect(screen.getByLabelText(/Summary/)).toHaveValue("Keep this answer");
});

it.each(["success", "failure"])(
	"ignores an obsolete dependent request's late %s",
	async (outcome) => {
		const pending = new Map<
			string,
			{
				resolve: (value: unknown) => void;
				reject: (error: Error) => void;
			}
		>();
		mockGetOptions.mockImplementation(
			(_form, _field, inputs) =>
				new Promise((resolve, reject) =>
					pending.set(inputs.country, { resolve, reject }),
				),
		);
		const { user } = renderWithProviders(
			<FormRenderer
				form={makeForm([
					{
						name: "country",
						label: "Country",
						type: "text",
						required: true,
					},
					{
						name: "owner",
						label: "Owner",
						type: "select",
						has_dynamic_options: true,
						data_provider_inputs: {
							country: {
								mode: "fieldRef",
								field_name: "country",
							},
						},
						auto_fill: { email: "email" },
					},
					{ name: "email", label: "Email", type: "text" },
				])}
			/>,
		);
		const country = await screen.findByLabelText(/Country/);
		fireEvent.change(country, { target: { value: "A" } });
		await waitFor(() => expect(pending.has("A")).toBe(true));
		fireEvent.change(country, { target: { value: "B" } });
		await waitFor(() => expect(pending.has("B")).toBe(true));
		pending.get("B")!.resolve([
			{
				value: "b",
				label: "Current owner",
				metadata: { email: "current@example.com" },
			},
		]);
		await waitFor(() =>
			expect(screen.getByLabelText(/Email/)).toHaveValue(
				"current@example.com",
			),
		);
		if (outcome === "success")
			pending.get("A")!.resolve([
				{
					value: "a",
					label: "Obsolete owner",
					metadata: { email: "obsolete@example.com" },
				},
			]);
		else pending.get("A")!.reject(new Error("Obsolete failure"));
		await user.click(screen.getByRole("combobox"));
		expect(
			screen.getByRole("option", { name: "Current owner" }),
		).toBeVisible();
		expect(
			screen.queryByRole("option", { name: "Obsolete owner" }),
		).not.toBeInTheDocument();
		expect(screen.getByLabelText(/Email/)).toHaveValue(
			"current@example.com",
		);
		expect(screen.queryByText("Obsolete failure")).not.toBeInTheDocument();
		await user.click(screen.getByRole("option", { name: "Current owner" }));
		fireEvent.change(country, { target: { value: "C" } });
		await waitFor(() => expect(pending.has("C")).toBe(true));
		expect(screen.getByRole("combobox")).not.toHaveTextContent(
			"Current owner",
		);
		fireEvent.change(country, { target: { value: "" } });
		pending.get("C")!.resolve([
			{
				value: "c",
				label: "Cleared owner",
				metadata: { email: "cleared@example.com" },
			},
		]);
		await waitFor(() =>
			expect(screen.getByRole("combobox")).toBeDisabled(),
		);
		expect(screen.getByLabelText(/Email/)).toHaveValue(
			"current@example.com",
		);
	},
);

it("starts independent choices while another field's request is pending", async () => {
	let finishSlow!: (value: unknown) => void;
	mockGetOptions.mockImplementation((_form, field) =>
		field === "slow"
			? new Promise((resolve) => {
					finishSlow = resolve;
				})
			: Promise.resolve([{ value: "ready", label: "Ready" }]),
	);
	renderWithProviders(
		<FormRenderer
			form={makeForm([
				{
					name: "slow",
					label: "Slow choices",
					type: "select",
					has_dynamic_options: true,
				},
				{
					name: "fast",
					label: "Fast choices",
					type: "select",
					has_dynamic_options: true,
				},
			])}
		/>,
	);
	await waitFor(() =>
		expect(mockGetOptions).toHaveBeenCalledWith(
			"form-1",
			"fast",
			undefined,
		),
	);
	finishSlow([]);
	await waitFor(() =>
		expect(screen.getAllByRole("combobox")).toHaveLength(2),
	);
});
