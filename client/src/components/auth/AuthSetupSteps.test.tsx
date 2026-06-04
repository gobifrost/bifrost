import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { AuthSetupSteps } from "./AuthSetupSteps";

describe("AuthSetupSteps", () => {
	it("calls onPasskeyRegister when passkey button clicked", async () => {
		const onPasskey = vi.fn().mockResolvedValue(undefined);
		render(
			<AuthSetupSteps
				email="x@y.com"
				onPasskeyRegister={onPasskey}
				onPasswordRegister={vi.fn()}
				isPending={false}
				error={null}
			/>,
		);
		await userEvent.click(screen.getByRole("button", { name: /set up passkey/i }));
		expect(onPasskey).toHaveBeenCalled();
	});

	it("calls onPasswordRegister with password", async () => {
		const onPwd = vi.fn().mockResolvedValue(undefined);
		render(
			<AuthSetupSteps
				email="x@y.com"
				onPasskeyRegister={vi.fn()}
				onPasswordRegister={onPwd}
				isPending={false}
				error={null}
			/>,
		);
		await userEvent.click(screen.getByRole("button", { name: /use password instead/i }));
		await userEvent.type(screen.getByLabelText("Password"), "secret123");
		await userEvent.type(
			screen.getByLabelText(/confirm password/i),
			"secret123",
		);
		await userEvent.click(screen.getByRole("button", { name: /create account/i }));
		expect(onPwd).toHaveBeenCalledWith("secret123");
	});

	it("blocks submission until the passwords match", async () => {
		const onPwd = vi.fn().mockResolvedValue(undefined);
		render(
			<AuthSetupSteps
				email="x@y.com"
				onPasskeyRegister={vi.fn()}
				onPasswordRegister={onPwd}
				isPending={false}
				error={null}
			/>,
		);
		await userEvent.click(
			screen.getByRole("button", { name: /use password instead/i }),
		);
		await userEvent.type(screen.getByLabelText("Password"), "secret123");
		await userEvent.type(
			screen.getByLabelText(/confirm password/i),
			"secret124",
		);

		expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /create account/i }),
		).toBeDisabled();
		expect(onPwd).not.toHaveBeenCalled();
	});

	it("displays error when provided", () => {
		render(
			<AuthSetupSteps
				email="x@y.com"
				onPasskeyRegister={vi.fn()}
				onPasswordRegister={vi.fn()}
				isPending={false}
				error="Something went wrong"
			/>,
		);
		expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
	});
});
