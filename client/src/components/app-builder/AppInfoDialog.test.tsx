import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";

import { renderWithProviders, screen } from "@/test-utils";
import { AppInfoDialog } from "./AppInfoDialog";

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAnywhere: () => true,
		canAt: () => true,
	}),
}));

const mockBumpEntityLogo = vi.fn();

const existingApp = {
	id: "app-1",
	name: "Logo App",
	slug: "logo-app",
	description: "Existing app",
	organization_id: null,
	access_level: "authenticated",
	role_ids: [],
	repo_path: "apps/logo-app",
};

vi.mock("@/components/LogoDropZone", () => ({
	LogoDropZone: ({
		onChange,
		previewUrl,
	}: {
		onChange?: () => void;
		previewUrl: string;
	}) => {
		return (
			<button
				type="button"
				aria-label="Upload fixture logo"
				title={previewUrl}
				onClick={onChange}
			>
				Upload
			</button>
		);
	},
}));

vi.mock("@/components/entityLogoVersions", async (importOriginal) => {
	const actual =
		await importOriginal<
			typeof import("@/components/entityLogoVersions")
		>();
	return {
		...actual,
		bumpEntityLogo: (...args: unknown[]) => mockBumpEntityLogo(...args),
	};
});

vi.mock("@/components/applications/AppReplacePathDialog", () => ({
	AppReplacePathDialog: () => null,
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

vi.mock("@/components/ui/combobox", () => ({
	Combobox: () => <button type="button">Select access level</button>,
}));

vi.mock("@/components/ui/command", () => ({
	Command: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
	CommandEmpty: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
	CommandGroup: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
	CommandInput: () => null,
	CommandItem: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
	CommandList: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
}));

vi.mock("@/hooks/useApplications", () => ({
	useApplication: () => ({
		data: existingApp,
		isLoading: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
	useCreateApplication: () => ({ mutateAsync: vi.fn(), isPending: false }),
	useUpdateApplication: () => ({ mutateAsync: vi.fn(), isPending: false }),
	useDeleteApplication: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => ({
		data: [],
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		isPlatformAdmin: true,
		user: { organizationId: "org-1" },
	}),
}));

describe("AppInfoDialog", () => {
	beforeEach(() => {
		mockBumpEntityLogo.mockClear();
	});

	it("invalidates cached logo metadata without discarding the settings draft", async () => {
		const user = userEvent.setup();
		const { queryClient } = renderWithProviders(
			<AppInfoDialog appSlug="logo-app" open onOpenChange={vi.fn()} />,
		);
		const keys = [
			["get", "/api/applications"],
			[
				"get",
				"/api/applications/{slug}",
				{ params: { path: { slug: "logo-app" } } },
			],
			["get", "/api/home"],
		];
		for (const key of keys) {
			queryClient.setQueryDefaults(key, { gcTime: Infinity });
			queryClient.setQueryData(key, { logo_url: null });
		}
		const name = screen.getByRole("textbox", { name: /^Name$/ });
		await user.clear(name);
		await user.type(name, "Unsaved app name");
		await user.click(
			screen.getByRole("button", { name: "Upload fixture logo" }),
		);
		for (const key of keys)
			expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true);
		expect(mockBumpEntityLogo).toHaveBeenCalledWith("app", "app-1");
		expect(name).toHaveValue("Unsaved app name");
	});
});
