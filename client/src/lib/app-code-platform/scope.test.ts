import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-router-dom", () => ({
	Outlet: vi.fn(),
	useLocation: vi.fn(),
	useMatch: vi.fn(),
	useOutletContext: vi.fn(),
	useResolvedPath: vi.fn(),
}));

import {
	useLocation as useRouterLocation,
	type Location,
} from "react-router-dom";
import { useAppBuilderStore } from "@/stores/app-builder.store";
import { createPlatformScope } from "./scope";

const mockedUseRouterLocation = vi.mocked(useRouterLocation);

function setRouterPath(pathname: string) {
	mockedUseRouterLocation.mockReturnValue({
		pathname,
		search: "?tab=current",
		hash: "#details",
		state: { from: "test" },
		key: "abc123",
		unstable_mask: undefined,
	} as Location);
}

function renderScopedLocation() {
	const scope = createPlatformScope();
	const useScopedLocation = scope.useLocation as typeof useRouterLocation;
	return renderHook(() => useScopedLocation());
}

afterEach(() => {
	useAppBuilderStore.getState().setAppContext("", false);
	vi.clearAllMocks();
});

describe("createPlatformScope useLocation", () => {
	it("returns app-relative pathnames for preview routes", () => {
		useAppBuilderStore.getState().setAppContext("demo-app", true);
		setRouterPath("/apps/demo-app/preview/reports/margins");

		const { result } = renderScopedLocation();

		expect(result.current).toMatchObject({
			pathname: "/reports/margins",
			search: "?tab=current",
			hash: "#details",
			state: { from: "test" },
			key: "abc123",
		});
	});

	it("returns app-relative pathnames for published routes", () => {
		useAppBuilderStore.getState().setAppContext("demo-app", false);
		setRouterPath("/apps/demo-app/reports/margins");

		const { result } = renderScopedLocation();

		expect(result.current.pathname).toBe("/reports/margins");
	});
});
