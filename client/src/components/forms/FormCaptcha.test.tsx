import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockAuthFetch = vi.fn();
const mockSolveChallenge = vi.fn();
vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));
vi.mock("altcha/lib", () => ({
	solveChallenge: (...args: unknown[]) => mockSolveChallenge(...args),
}));
vi.mock("hash-wasm", () => ({
	createSHA256: vi.fn(() => Promise.resolve({})),
	pbkdf2: vi.fn(),
}));

import { FormCaptcha } from "./FormCaptcha";

function jsonResponse(body: unknown, ok = true) {
	return {
		ok,
		json: async () => body,
	} as Response;
}

beforeEach(() => {
	mockAuthFetch.mockReset();
	mockSolveChallenge.mockReset();
});

describe("FormCaptcha", () => {
	it("loads, solves, and emits a session-bound challenge payload", async () => {
		const challenge = {
			parameters: { algorithm: "PBKDF2/SHA-256" },
			signature: "signed",
		};
		const solution = { counter: 42, derivedKey: "00abc" };
		mockAuthFetch.mockResolvedValueOnce(jsonResponse(challenge));
		mockSolveChallenge.mockResolvedValueOnce(solution);
		const onPayloadChange = vi.fn();
		const { user } = renderWithProviders(
			<FormCaptcha formId="form-1" onPayloadChange={onPayloadChange} />,
		);

		const checkbox = await screen.findByRole("checkbox", {
			name: "I'm not a robot",
		});
		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/forms/form-1/captcha/challenge",
			expect.objectContaining({ method: "POST" }),
		);
		await user.click(checkbox);

		await waitFor(() => expect(mockSolveChallenge).toHaveBeenCalledTimes(1));
		await waitFor(() => expect(screen.getByText("Verified")).toBeInTheDocument());
		const payload = onPayloadChange.mock.calls.at(-1)?.[0] as string;
		expect(JSON.parse(globalThis.atob(payload))).toEqual({ challenge, solution });
	});

	it("shows a retry action when the challenge cannot load", async () => {
		mockAuthFetch.mockResolvedValueOnce(jsonResponse({}, false));
		renderWithProviders(
			<FormCaptcha formId="form-1" onPayloadChange={vi.fn()} />,
		);

		expect(
			await screen.findByText("Verification could not be loaded."),
		).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});
});
