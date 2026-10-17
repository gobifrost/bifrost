import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

import { AuditPageLayout } from "./AuditPageLayout";

function Location() {
	const location = useLocation();
	return <output aria-label="Current route">{location.pathname}</output>;
}

function ActivityPage() {
	return (
		<>
			<AuditPageLayout view="activity">
				<p>Activity content</p>
			</AuditPageLayout>
			<Location />
		</>
	);
}

function AccessChecksPage() {
	return (
		<>
			<AuditPageLayout view="access-checks">
				<p>Access checks content</p>
			</AuditPageLayout>
			<Location />
		</>
	);
}

describe("AuditPageLayout", () => {
	it("keeps the current view selected and navigates between audit routes", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route path="/audit" element={<ActivityPage />} />
				<Route
					path="/audit/access-checks"
					element={<AccessChecksPage />}
				/>
			</Routes>,
			{ initialEntries: ["/audit"] },
		);

		expect(screen.getByRole("heading", { name: "Auditing" })).toBeVisible();
		expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(
			screen.getByRole("tabpanel", { name: "Activity" }),
		).toHaveTextContent("Activity content");

		await user.click(screen.getByRole("tab", { name: "Access Checks" }));

		await waitFor(() =>
			expect(screen.getByLabelText("Current route")).toHaveTextContent(
				"/audit/access-checks",
			),
		);
		expect(
			screen.getByRole("tab", { name: "Access Checks" }),
		).toHaveAttribute("aria-selected", "true");
		expect(
			screen.getByRole("tabpanel", { name: "Access Checks" }),
		).toHaveTextContent("Access checks content");
	});

	it("renders header actions beside the shared title", () => {
		renderWithProviders(
			<AuditPageLayout
				view="activity"
				actions={<button type="button">Refresh activity</button>}
			>
				<p>Activity content</p>
			</AuditPageLayout>,
		);

		expect(
			screen.getByRole("button", { name: "Refresh activity" }),
		).toBeVisible();
	});

	it("keeps keyboard focus on the selected tab after route navigation", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route path="/audit" element={<ActivityPage />} />
				<Route
					path="/audit/access-checks"
					element={<AccessChecksPage />}
				/>
			</Routes>,
			{ initialEntries: ["/audit"] },
		);

		screen.getByRole("tab", { name: "Activity" }).focus();
		await user.keyboard("{ArrowRight}");

		await waitFor(() =>
			expect(screen.getByLabelText("Current route")).toHaveTextContent(
				"/audit/access-checks",
			),
		);
		expect(
			screen.getByRole("tab", { name: "Access Checks" }),
		).toHaveAttribute("aria-selected", "true");
		expect(
			screen.getByRole("tabpanel", { name: "Access Checks" }),
		).toHaveTextContent("Access checks content");
		expect(
			screen.getByRole("tab", { name: "Access Checks" }),
		).toHaveFocus();

		await user.keyboard("{ArrowLeft}");

		await waitFor(() =>
			expect(screen.getByLabelText("Current route")).toHaveTextContent(
				"/audit",
			),
		);
		expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		expect(
			screen.getByRole("tabpanel", { name: "Activity" }),
		).toHaveTextContent("Activity content");
		expect(screen.getByRole("tab", { name: "Activity" })).toHaveFocus();
	});

	it("does not move focus when entered through an access-checks deep link", () => {
		renderWithProviders(
			<Routes>
				<Route path="/audit" element={<ActivityPage />} />
				<Route
					path="/audit/access-checks"
					element={<AccessChecksPage />}
				/>
			</Routes>,
			{ initialEntries: ["/audit/access-checks"] },
		);

		expect(
			screen.getByRole("tab", { name: "Access Checks" }),
		).not.toHaveFocus();
	});
});
