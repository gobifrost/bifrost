import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { DiagnosticsPage } from "./DiagnosticsPage";

const authority = vi.hoisted(() => ({ permissions: new Set<string>(), loading: false }));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		isLoading: authority.loading,
		canAt: (permission: string, target: { kind: string }) => target.kind === "global" && authority.permissions.has(permission),
	}),
}));
vi.mock("./components/WorkersTab", () => ({ WorkersTab: () => <p>Worker status</p> }));
vi.mock("./components/SchedulerTab", () => ({ SchedulerTab: () => <p>Scheduler status</p> }));
beforeEach(() => { authority.permissions.clear(); authority.loading = false; });
describe("DiagnosticsPage admission", () => {
	it("admits named Global readers without platform-admin membership", () => {
		authority.permissions.add("platform.read");
		authority.permissions.add("settings.read");
		renderWithProviders(<DiagnosticsPage />);
		expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeVisible();
		expect(screen.getByText("Worker status")).toBeVisible();
	});
	it("keeps child diagnostics unmounted when an independent Global grant is missing", () => {
		authority.permissions.add("platform.read");
		renderWithProviders(<DiagnosticsPage />);
		expect(screen.getByText("You do not have permission to view diagnostics.")).toBeVisible();
		expect(screen.queryByText("Worker status")).not.toBeInTheDocument();
	});
	it("waits for authorization before showing diagnostics", () => {
		authority.loading = true;
		renderWithProviders(<DiagnosticsPage />);
		expect(screen.getByRole("status")).toHaveTextContent("Loading diagnostics…");
		expect(screen.queryByText("Worker status")).not.toBeInTheDocument();
	});
});
