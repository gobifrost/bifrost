/**
 * Component tests for FormPreview.
 *
 * Disabled read-only preview of a form. All inputs should be rendered
 * disabled, required fields should show a *, textarea / checkbox / select
 * variants should route to the right element, and the empty state should
 * show when no fields exist.
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { FormPreview } from "./FormPreview";
import type { FormField } from "@/lib/client-types";

describe("FormPreview", () => {
	it("renders a disabled shared datetime control with the local default", () => {
		renderWithProviders(
			<FormPreview
				formName="F"
				formDescription=""
				fields={[
					{
						name: "appointment",
						label: "Appointment",
						type: "datetime",
						default_value: "2026-11-10T09:00",
					},
				]}
			/>,
		);
		const control = screen.getByLabelText("Appointment");
		expect(control).toHaveTextContent("Nov 10, 2026 · 9:00 AM");
		expect(control).toBeDisabled();
	});

	it("renders a disabled date control with its default calendar date", () => {
		renderWithProviders(
			<FormPreview
				formName="F"
				formDescription=""
				fields={[
					{
						name: "start_date",
						label: "Start Date",
						type: "date",
						required: false,
						default_value: "2026-11-10",
					},
				]}
			/>,
		);
		const input = screen.getByLabelText("Start Date");
		expect(input).toHaveTextContent("Nov 10, 2026");
		expect(input).toBeDisabled();
	});

	it("renders the form title and description", () => {
		renderWithProviders(
			<FormPreview
				formName="My Form"
				formDescription="Some description"
				fields={[]}
			/>,
		);

		expect(screen.getByText("My Form")).toBeInTheDocument();
		expect(screen.getByText("Some description")).toBeInTheDocument();
	});

	it("shows the empty state when no fields are provided", () => {
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={[]} />,
		);

		expect(
			screen.getByText(/add fields to see the form preview/i),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /submit/i }),
		).not.toBeInTheDocument();
	});

	it("renders a disabled text input for type=text", () => {
		const fields: FormField[] = [
			{
				name: "first",
				label: "First Name",
				type: "text",
				required: false,
				placeholder: "Your name",
			},
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		// Label is not htmlFor-linked for non-checkbox fields; locate input by
		// placeholder instead.
		const input = screen.getByPlaceholderText("Your name");
		expect(input).toBeDisabled();
		// Label is still rendered.
		expect(screen.getByText("First Name")).toBeInTheDocument();
	});

	it("marks required fields with a red asterisk", () => {
		const fields: FormField[] = [
			{ name: "email", label: "Email", type: "email", required: true },
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		// There should be a star next to the Email label
		expect(screen.getByText("*")).toBeInTheDocument();
	});

	it("renders a disabled textarea for type=textarea", () => {
		const fields: FormField[] = [
			{
				name: "bio",
				label: "Bio",
				type: "textarea",
				required: false,
				placeholder: "Tell us",
			},
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		const textarea = screen.getByPlaceholderText("Tell us");
		expect(textarea.tagName).toBe("TEXTAREA");
		expect(textarea).toBeDisabled();
	});

	it("renders a disabled checkbox with the label when type=checkbox", () => {
		const fields: FormField[] = [
			{
				name: "agree",
				label: "I agree",
				type: "checkbox",
				required: false,
			},
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		const cb = screen.getByLabelText(/i agree/i);
		expect(cb).toBeDisabled();
		expect((cb as HTMLInputElement).type).toBe("checkbox");
	});

	it("renders help text below a field when help_text is provided", () => {
		const fields: FormField[] = [
			{
				name: "phone",
				label: "Phone",
				type: "text",
				required: false,
				help_text: "Include country code",
			},
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		expect(screen.getByText(/include country code/i)).toBeInTheDocument();
	});

	it("renders a disabled Submit button when there are fields", () => {
		const fields: FormField[] = [
			{ name: "x", label: "X", type: "text", required: false },
		];
		renderWithProviders(
			<FormPreview formName="F" formDescription="" fields={fields} />,
		);

		const submit = screen.getByRole("button", { name: /submit/i });
		expect(submit).toBeDisabled();
	});
});
