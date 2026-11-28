import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { query, create, update, remove } = vi.hoisted(() => ({
	query: vi.fn(),
	create: vi.fn(),
	update: vi.fn(),
	remove: vi.fn(),
}));
vi.mock("@/services/federated-credentials", () => ({
	useFederatedCredentials: () => query(),
	useCreateFederatedCredential: () => ({
		mutateAsync: create,
		isPending: false,
	}),
	useUpdateFederatedCredential: () => ({
		mutateAsync: update,
		isPending: false,
	}),
	useDeleteFederatedCredential: () => ({
		mutateAsync: remove,
		isPending: false,
	}),
}));
import { FederatedCredentialsPanel } from "./FederatedCredentialsPanel";

const credential = {
	id: "cred",
	identity_id: "identity",
	name: "Release Job",
	issuer: "https://issuer.example",
	audience: "https://bifrost.example",
	subject: "workload:example",
	subject_token_type: "urn:ietf:params:oauth:token-type:id_token",
	claims: { repository_id: "1234" },
	token_ttl_seconds: 300,
	enabled: true,
};

describe("FederatedCredentialsPanel", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		query.mockReturnValue({ data: [], isLoading: false, isError: false });
		create.mockResolvedValue(credential);
		update.mockResolvedValue(credential);
		remove.mockResolvedValue(undefined);
	});
	it("lets an operator create exact claim restrictions for this identity", async () => {
		const user = userEvent.setup();
		render(<FederatedCredentialsPanel identityId="identity" canManage />);
		await user.click(
			screen.getByRole("button", { name: "New Federated Credential" }),
		);
		const form = screen.getByRole("dialog", {
			name: "New Federated Credential",
		});
		await user.type(within(form).getByLabelText("Name"), "Release Job");
		await user.type(
			within(form).getByLabelText("Issuer URL"),
			"https://issuer.example",
		);
		await user.clear(within(form).getByLabelText("Audience"));
		await user.type(
			within(form).getByLabelText("Audience"),
			"https://bifrost.example",
		);
		await user.type(
			within(form).getByLabelText("Subject"),
			"workload:example",
		);
		await user.click(
			within(form).getByRole("button", { name: "Add Claim" }),
		);
		await user.type(
			within(form).getByLabelText("Claim Name 1"),
			"repository_id",
		);
		await user.type(within(form).getByLabelText("Exact Value 1"), "1234");
		await user.click(
			within(form).getByRole("button", { name: "Save Credential" }),
		);
		expect(create).toHaveBeenCalledWith({
			params: { path: { identity_id: "identity" } },
			body: {
				name: credential.name,
				enabled: true,
				issuer: credential.issuer,
				discovery_url: null,
				audience: credential.audience,
				subject: credential.subject,
				subject_token_type: credential.subject_token_type,
				claims: credential.claims,
				token_ttl_seconds: 300,
			},
		});
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
	});
	it("shows a save refusal without losing the operator's input", async () => {
		query.mockReturnValue({ data: [credential] });
		update.mockRejectedValue(
			new Error("You cannot delegate this identity."),
		);
		const user = userEvent.setup();
		render(<FederatedCredentialsPanel identityId="identity" canManage />);
		await user.click(
			screen.getByRole("button", { name: "Edit Release Job" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Save Credential" }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"You cannot delegate this identity.",
		);
		expect(screen.getByLabelText("Subject")).toHaveValue(
			"workload:example",
		);
	});
	it("keeps a read-only list free of mutation controls", () => {
		query.mockReturnValue({ data: [credential] });
		render(
			<FederatedCredentialsPanel
				identityId="identity"
				canManage={false}
			/>,
		);
		expect(screen.getByText("workload:example")).toBeVisible();
		expect(
			screen.queryByRole("button", { name: /New|Edit|Disable|Delete/ }),
		).not.toBeInTheDocument();
	});
	it("disables a credential and confirms deletion separately", async () => {
		query.mockReturnValue({ data: [credential] });
		const user = userEvent.setup();
		render(<FederatedCredentialsPanel identityId="identity" canManage />);
		await user.click(
			screen.getByRole("button", { name: "Disable Release Job" }),
		);
		expect(update).toHaveBeenCalledWith({
			params: {
				path: { identity_id: "identity", credential_id: "cred" },
			},
			body: { enabled: false },
		});
		await user.click(
			screen.getByRole("button", { name: "Delete Release Job" }),
		);
		expect(remove).not.toHaveBeenCalled();
		await user.click(
			screen.getByRole("button", {
				name: "Delete Credential",
			}),
		);
		expect(remove).toHaveBeenCalledWith({
			params: {
				path: { identity_id: "identity", credential_id: "cred" },
			},
		});
	});
});
