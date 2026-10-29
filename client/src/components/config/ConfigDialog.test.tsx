import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { ConfigDialog } from "./ConfigDialog";

const update = vi.fn();
const create = vi.fn();
const mockUseAuth = vi.fn();
const mockAuthorization = vi.fn();
vi.mock("@/hooks/useConfig", () => ({
	useSetConfig: () => ({ mutateAsync: create, isPending: false }),
	useUpdateConfig: () => ({ mutateAsync: update, isPending: false }),
}));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockAuthorization(),
}));
const base = {
	id: "11111111-1111-4111-8111-111111111111",
	key: "settings",
	scope: "GLOBAL" as const,
	org_id: null,
	description: "Configuration settings",
	required: false,
	position: 0,
	integration_name: null,
};
beforeEach(() => {
	vi.clearAllMocks();
	mockUseAuth.mockReturnValue({
		isPlatformAdmin: false,
		user: { organizationId: "org-home" },
	});
	mockAuthorization.mockReturnValue({
		canAnywhere: () => false,
		canAt: () => true,
	});
	update.mockReset();
	create.mockReset();
});

describe("ConfigDialog value and save contract", () => {
	it("preserves JSON values through a failed save and retry", async () => {
		const value = { enabled: false, interval: 0, region: "east" };
		update
			.mockRejectedValueOnce({ detail: "Synthetic save failure" })
			.mockResolvedValueOnce({});
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<ConfigDialog
				config={{ ...base, type: "json", value }}
				open
				onClose={onClose}
			/>,
		);
		const field = screen.getByRole("textbox", { name: "Value" });
		expect(JSON.parse((field as HTMLTextAreaElement).value)).toEqual(value);
		expect(
			screen.getByRole("combobox", { name: "Type" }),
		).toHaveTextContent("JSON");
		const save = screen.getByRole("button", { name: "Update" });
		await waitFor(() => expect(save).toBeEnabled());
		await user.click(save);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Synthetic save failure",
		);
		expect(onClose).not.toHaveBeenCalled();
		expect(JSON.parse((field as HTMLTextAreaElement).value)).toEqual(value);
		await user.click(save);
		await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
		expect(update).toHaveBeenCalledTimes(2);
		expect(update.mock.calls[1][0]).toEqual(update.mock.calls[0][0]);
		expect(JSON.parse(update.mock.calls[0][0].body.value)).toEqual(value);
	});
	it("omits an unchanged secret from the update", async () => {
		update.mockResolvedValue({});
		const { user } = renderWithProviders(
			<ConfigDialog
				config={{ ...base, type: "secret", value: "[SECRET]" }}
				open
				onClose={vi.fn()}
			/>,
		);
		expect(screen.getByLabelText("Value")).toHaveValue("");
		const save = screen.getByRole("button", { name: "Update" });
		await waitFor(() => expect(save).toBeEnabled());
		await user.click(save);
		await waitFor(() => expect(update).toHaveBeenCalledOnce());
		expect(update.mock.calls[0][0].body).not.toHaveProperty("value");
	});
	it("validates empty creation before calling the API", async () => {
		const { user } = renderWithProviders(
			<ConfigDialog open onClose={vi.fn()} />,
		);
		await user.click(screen.getByRole("button", { name: "Create" }));
		expect(await screen.findByText("Key is required")).toBeVisible();
		expect(screen.getByText("Value is required")).toBeVisible();
		expect(create).not.toHaveBeenCalled();
	});

	it("does not treat a persisted global config as the home organization", async () => {
		mockAuthorization.mockReturnValue({
			canAnywhere: () => false,
			canAt: (_permission: string, target: { kind: string }) =>
				target.kind === "org",
		});
		const { user } = renderWithProviders(
			<ConfigDialog
				config={{ ...base, type: "string", value: "existing" }}
				open
				onClose={vi.fn()}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Update" }));
		expect(update).not.toHaveBeenCalled();
	});

	it("allows a global config writer to update a persisted global config", async () => {
		update.mockResolvedValue({});
		mockAuthorization.mockReturnValue({
			canAnywhere: () => true,
			canAt: (_permission: string, target: { kind: string }) =>
				target.kind === "global",
		});
		const { user } = renderWithProviders(
			<ConfigDialog
				config={{ ...base, type: "string", value: "existing" }}
				open
				onClose={vi.fn()}
			/>,
		);
		await user.click(screen.getByRole("button", { name: "Update" }));
		await waitFor(() => expect(update).toHaveBeenCalledOnce());
	});
});
