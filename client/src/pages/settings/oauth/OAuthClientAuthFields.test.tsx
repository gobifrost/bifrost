import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import {
	OAuthClientAuthFields,
	type OAuthClientAuthDraft,
} from "./OAuthClientAuthFields";

const base: OAuthClientAuthDraft = {
	token_endpoint_auth_method: "client_secret_post",
	private_key: "",
	private_key_kid: "",
	private_key_alg: "RS256",
	clear_private_key: false,
	scopes: "openid email profile",
};

describe("OAuthClientAuthFields", () => {
	it("shows revocation for a stored key on a secret method", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<OAuthClientAuthFields
				{...base}
				idPrefix="test"
				privateKeySet
				onChange={onChange}
			/>,
		);

		const checkbox = screen.getByRole("checkbox", {
			name: /remove the stored private key/i,
		});
		expect(checkbox).toBeInTheDocument();
		// Key material fields only render for private_key_jwt.
		expect(
			screen.queryByLabelText(/private key \(pem\)/i),
		).not.toBeInTheDocument();

		fireEvent.click(checkbox);
		expect(onChange).toHaveBeenCalledWith({
			clear_private_key: true,
			private_key: "",
			private_key_kid: "",
		});
	});

	it("locks the kid until a replacement key is supplied", () => {
		const onChange = vi.fn();
		const { rerender } = renderWithProviders(
			<OAuthClientAuthFields
				{...base}
				token_endpoint_auth_method="private_key_jwt"
				privateKeySet
				private_key_kid="old-kid"
				idPrefix="test"
				onChange={onChange}
			/>,
		);

		const kid = screen.getByLabelText(/key id \(kid\)/i);
		expect(kid).toBeDisabled();
		expect(
			screen.getByText(/supply a new key to change the key id/i),
		).toBeInTheDocument();

		fireEvent.change(screen.getByLabelText(/private key \(pem\)/i), {
			target: { value: "-----BEGIN PRIVATE KEY-----" },
		});
		expect(onChange).toHaveBeenCalledWith({
			private_key: "-----BEGIN PRIVATE KEY-----",
		});

		rerender(
			<OAuthClientAuthFields
				{...base}
				token_endpoint_auth_method="private_key_jwt"
				privateKeySet
				private_key="-----BEGIN PRIVATE KEY-----"
				private_key_kid="old-kid"
				idPrefix="test"
				onChange={onChange}
			/>,
		);
		expect(screen.getByLabelText(/key id \(kid\)/i)).toBeEnabled();
	});

	it("warns when revoking while private_key_jwt is still selected", () => {
		renderWithProviders(
			<OAuthClientAuthFields
				{...base}
				token_endpoint_auth_method="private_key_jwt"
				privateKeySet
				clear_private_key
				idPrefix="test"
				onChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByText(/choose a client-secret method to revoke/i),
		).toBeInTheDocument();
	});

	it("reports scope edits", () => {
		const onChange = vi.fn();
		renderWithProviders(
			<OAuthClientAuthFields
				{...base}
				idPrefix="test"
				privateKeySet={false}
				onChange={onChange}
			/>,
		);

		fireEvent.change(screen.getByLabelText(/scopes/i), {
			target: { value: "openid email groups" },
		});
		expect(onChange).toHaveBeenCalledWith({
			scopes: "openid email groups",
		});
	});
});
