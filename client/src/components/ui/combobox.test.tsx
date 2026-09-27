import { useState } from "react";
import { Shield } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { Combobox } from "./combobox";

describe("Combobox", () => {
	it("keeps a long selected label on one truncated line", () => {
		const label =
			"Active Directory user audit with an exceptionally long display name";
		renderWithProviders(
			<Combobox
				options={[{ value: "directory-audit", label }]}
				value="directory-audit"
			/>,
		);

		expect(screen.getByRole("combobox")).toHaveClass("h-11", "sm:h-10");
		expect(screen.getByText(label)).toHaveClass("truncate");
	});

	it("keeps the selected value visible when options are unavailable", () => {
		renderWithProviders(<Combobox options={[]} value="ticket_id" placeholder="Pick key" />);
		expect(screen.getByRole("combobox")).toHaveTextContent("ticket_id");
	});
	it("connects validation feedback to the trigger", () => {
		renderWithProviders(<><Combobox options={[]} aria-invalid="true" aria-describedby="lookup-error" /><p id="lookup-error">Could not load values.</p></>);
		expect(screen.getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
		expect(screen.getByRole("combobox")).toHaveAccessibleDescription("Could not load values.");
	});
	it("renders optional icons for selected and listed options", async () => {
		const { user } = renderWithProviders(
			<Combobox
				options={[
					{ value: "role_based", label: "Role-based", icon: Shield },
				]}
				value="role_based"
				onValueChange={vi.fn()}
			/>,
		);

		expect(screen.getByRole("combobox").querySelector("svg")).toBeTruthy();
		await user.click(screen.getByRole("combobox"));
		expect(
			screen.getByRole("option", { name: "Role-based" }).querySelector(
				"svg",
			),
		).toBeTruthy();
	});
	it("filters options by literal label and value text", async () => {
		const { user } = renderWithProviders(
			<Combobox
				options={[
					{ value: "google/nano-banana", label: "Nano Banana" },
					{
						value: "deepseek/deepseek-v4-pro",
						label: "DeepSeek V4 Pro",
					},
					{ value: "openai/sora", label: "Sora" },
				]}
				onValueChange={vi.fn()}
				placeholder="Choose model"
				searchPlaceholder="Search models..."
			/>,
		);

		await user.click(screen.getByRole("combobox"));
		await user.type(
			screen.getByPlaceholderText("Search models..."),
			"banana",
		);

		expect(
			screen.getByRole("option", { name: "Nano Banana" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: "DeepSeek V4 Pro" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: "Sora" }),
		).not.toBeInTheDocument();
	});
	it("filters options by description text", async () => {
		const { user } = renderWithProviders(
			<Combobox
				options={[
					{
						value: "archive-library",
						label: "Archive document library",
						description: "Moves completed files from SharePoint",
					},
					{
						value: "audit-mailboxes",
						label: "Audit mailboxes",
						description: "Checks Exchange retention policies",
					},
				]}
				onValueChange={vi.fn()}
				placeholder="Choose workflow"
				searchPlaceholder="Search workflows..."
			/>,
		);

		await user.click(screen.getByRole("combobox"));
		await user.type(
			screen.getByPlaceholderText("Search workflows..."),
			"sharepoint",
		);

		expect(
			screen.getByRole("option", { name: /Archive document library/ }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("option", { name: /Audit mailboxes/ }),
		).not.toBeInTheDocument();
	});
	it("selects and clears a long option by keyboard and restores trigger focus", async () => {
		const label =
			"Customer reconciliation administrator with extended support permissions";
		function Field() {
			const [value, setValue] = useState("");
			return (
				<>
					<label htmlFor="review-choice">Reviewer</label>
					<Combobox
						id="review-choice"
						options={[{ value: "reviewer-1", label }]}
						value={value}
						onValueChange={setValue}
						placeholder="Choose reviewer"
						searchPlaceholder="Search reviewers"
					/>
				</>
			);
		}
		const { user } = renderWithProviders(<Field />);
		await user.click(screen.getByLabelText("Reviewer"));
		expect(
			await screen.findByPlaceholderText("Search reviewers"),
		).toHaveAccessibleName("Search reviewers");
		await user.type(
			await screen.findByPlaceholderText("Search reviewers"),
			"reconciliation",
		);
		await user.keyboard("{Enter}");
		expect(screen.getByLabelText("Reviewer")).toHaveTextContent(label);
		expect(screen.getByLabelText("Reviewer")).toHaveFocus();
		await user.keyboard("{Enter}");
		await user.keyboard("{Enter}");
		expect(screen.getByLabelText("Reviewer")).toHaveTextContent(
			"Choose reviewer",
		);
	});
});
