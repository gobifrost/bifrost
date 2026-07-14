/**
 * Component tests for ExecutionMetadataBar.
 *
 * The bar shows workflow name + status badge + four inline metadata fields.
 * The formatDate util is real (locale-dependent), so we only assert the
 * substrings the component itself produces (fallbacks + duration formatting),
 * and that each nullable field has a sensible default.
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ExecutionMetadataBar } from "./ExecutionMetadataBar";

describe("ExecutionMetadataBar — workflow name + status", () => {
	it("renders the workflow name as a heading", () => {
		renderWithProviders(
			<ExecutionMetadataBar workflowName="daily_report" status="Success" />,
		);
		expect(
			screen.getByRole("heading", { name: "daily_report" }),
		).toBeInTheDocument();
	});

	it("renders the status badge alongside the name", () => {
		renderWithProviders(
			<ExecutionMetadataBar workflowName="x" status="Running" />,
		);
		expect(screen.getByText(/running/i)).toBeInTheDocument();
	});
});

describe("ExecutionMetadataBar — fallbacks for missing fields", () => {
	it("shows 'Unknown' when executedByName is null", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Success"
				executedByName={null}
			/>,
		);
		expect(screen.getByText("Unknown")).toBeInTheDocument();
	});

	it("shows 'Global' when orgName is null", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Success"
				orgName={null}
			/>,
		);
		expect(screen.getByText("Global")).toBeInTheDocument();
	});

	it("shows 'Not started' when startedAt is null", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Pending"
				startedAt={null}
			/>,
		);
		expect(screen.getByText(/not started/i)).toBeInTheDocument();
	});

	it("shows 'In progress...' when durationMs is null", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Running"
				durationMs={null}
			/>,
		);
		expect(screen.getByText(/in progress/i)).toBeInTheDocument();
	});
});

describe("ExecutionMetadataBar — duration formatting", () => {
	it.each([
		[150, "150ms"],
		[1500, "1.5s"],
		[65_000, "1m 5s"],
	])("formats %dms as '%s'", (ms, expected) => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Success"
				durationMs={ms}
			/>,
		);
		expect(screen.getByText(expected)).toBeInTheDocument();
	});

	it("distinguishes total elapsed time from workflow execution time", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Success"
				durationMs={118}
				totalDurationMs={20_000}
			/>,
		);

		expect(screen.getByText("Total")).toBeInTheDocument();
		expect(screen.getByText("20.0s")).toBeInTheDocument();
		expect(screen.getByText("Workflow")).toBeInTheDocument();
		expect(screen.getByText("118ms")).toBeInTheDocument();
	});
});

describe("ExecutionMetadataBar — provided metadata", () => {
	it("renders executedByName and orgName when present", () => {
		renderWithProviders(
			<ExecutionMetadataBar
				workflowName="x"
				status="Success"
				executedByName="Alice"
				orgName="Acme"
			/>,
		);
		expect(screen.getByText("Alice")).toBeInTheDocument();
		expect(screen.getByText("Acme")).toBeInTheDocument();
	});
});
