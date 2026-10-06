import { beforeEach, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import { AppRouter } from "./AppRouter";
const state = vi.hoisted(() => ({
	query: vi.fn(),
	embed: false,
	shell: vi.fn(),
	refetch: vi.fn(),
}));
vi.mock("@/hooks/useApplications", () => ({
	useApplication: () => state.query(),
}));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ hasRole: () => state.embed }),
}));
vi.mock("@/lib/useDocumentChrome", () => ({ useDocumentChrome: () => {} }));
vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Bifrost",
}));
vi.mock("@/components/jsx-app/BundledAppShell", () => ({
	BundledAppShell: (props: unknown) => {
		state.shell(props);
		return <div>App content</div>;
	},
}));
vi.mock("@/components/layout/AppLayout", () => ({
	AppLayout: ({ children }: { children: React.ReactNode }) => (
		<section aria-label="App chrome">{children}</section>
	),
}));
beforeEach(() => {
	vi.clearAllMocks();
	state.embed = false;
	state.query.mockReturnValue({
		data: {
			id: "app-1",
			slug: "sample",
			name: "Sample",
			logo: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
			is_published: true,
			app_model: "inline_v1",
		},
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: state.refetch,
	});
});
function renderRoute(preview = false) {
	return renderWithProviders(
		<Routes>
			<Route
				path="/apps/:applicationId/*"
				element={<AppRouter preview={preview} />}
			/>
		</Routes>,
		{ initialEntries: ["/apps/sample"] },
	);
}
it("retries unavailable app metadata without mounting a broken app", async () => {
	state.query.mockReturnValue({
		error: new Error("failed"),
		refetch: state.refetch,
		isFetching: false,
	});
	const { user } = renderRoute();
	await user.click(screen.getByRole("button", { name: "Try again" }));
	expect(state.refetch).toHaveBeenCalledOnce();
	expect(state.shell).not.toHaveBeenCalled();
	expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
		"could not be loaded",
	);
});
it("keeps V1 preview draft routing inside app chrome", () => {
	state.query.mockReturnValue({
		data: {
			id: "app-1",
			slug: "sample",
			name: "Sample",
			logo: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
			is_published: false,
			app_model: "inline_v1",
		},
	});
	renderRoute(true);
	expect(
		screen.getByRole("region", { name: "App chrome" }),
	).toBeInTheDocument();
	expect(state.shell).toHaveBeenCalledWith(
		expect.objectContaining({
			appId: "app-1",
			isPreview: true,
			appName: "Sample",
			appLogo: "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
		}),
	);
});
it("leaves standalone apps full-page", () => {
	state.query.mockReturnValue({
		data: {
			id: "app-1",
			slug: "sample",
			name: "Sample",
			is_published: true,
			app_model: "standalone_v2",
			solution_id: "solution-1",
		},
	});
	renderRoute();
	expect(state.shell).toHaveBeenCalledWith(
		expect.objectContaining({ solutionId: "solution-1" }),
	);
	expect(screen.getByText("App content")).toBeInTheDocument();
	expect(
		screen.queryByRole("region", { name: "App chrome" }),
	).not.toBeInTheDocument();
});
it("does not offer host navigation to an embedded user in an unavailable app", () => {
	state.embed = true;
	state.query.mockReturnValue({
		error: new Error("failed"),
		refetch: state.refetch,
	});
	renderRoute();
	expect(
		screen.getByRole("button", { name: "Try again" }),
	).toBeInTheDocument();
	expect(screen.getAllByRole("button")).toHaveLength(1);
});
