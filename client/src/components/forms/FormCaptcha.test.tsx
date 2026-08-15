import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockAuthFetch = vi.fn();
const mockSolveChallenge = vi.fn();
const mockNativeDeriveKey = vi.fn();
const mockWasmPbkdf2 = vi.fn();
vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));
vi.mock("altcha/lib", () => ({
	solveChallenge: (...args: unknown[]) => mockSolveChallenge(...args),
	pbkdf2: {
		deriveKey: (...args: unknown[]) => mockNativeDeriveKey(...args),
	},
}));
vi.mock("hash-wasm", () => ({
	createSHA256: vi.fn(() => Promise.resolve({ name: "sha256" })),
	pbkdf2: (...args: unknown[]) => mockWasmPbkdf2(...args),
}));

import { deriveFormCaptchaKey, FormCaptcha } from "./FormCaptcha";

function jsonResponse(body: unknown, ok = true) {
	return {
		ok,
		json: async () => body,
	} as Response;
}

beforeEach(() => {
	mockAuthFetch.mockReset();
	mockSolveChallenge.mockReset();
	mockNativeDeriveKey.mockReset();
	mockWasmPbkdf2.mockReset();
	vi.unstubAllGlobals();
});

describe("FormCaptcha", () => {
	it("uses native WebCrypto for the production HTTPS proof path", async () => {
		const expected = { derivedKey: new Uint8Array([1, 2, 3]) };
		mockNativeDeriveKey.mockResolvedValueOnce(expected);
		vi.stubGlobal("crypto", { subtle: {} });
		const parameters = {
			algorithm: "PBKDF2/SHA-256",
			cost: 2500,
			keyLength: 32,
			keyPrefix: "00",
			nonce: "nonce",
			salt: "salt",
		};
		const salt = new Uint8Array([4]);
		const password = new Uint8Array([5]);

		await expect(
			deriveFormCaptchaKey(parameters, salt, password),
		).resolves.toBe(expected);
		expect(mockNativeDeriveKey).toHaveBeenCalledWith(
			parameters,
			salt,
			password,
		);
		expect(mockWasmPbkdf2).not.toHaveBeenCalled();
	});

	it("keeps the WASM proof path for insecure local origins", async () => {
		const derivedKey = new Uint8Array([6, 7, 8]);
		mockWasmPbkdf2.mockResolvedValueOnce(derivedKey);
		vi.stubGlobal("crypto", {});

		await expect(
			deriveFormCaptchaKey(
				{
					algorithm: "PBKDF2/SHA-256",
					cost: 2500,
					keyLength: 32,
					keyPrefix: "00",
					nonce: "nonce",
					salt: "salt",
				},
				new Uint8Array([9]),
				new Uint8Array([10]),
			),
		).resolves.toEqual({ derivedKey });
		expect(mockNativeDeriveKey).not.toHaveBeenCalled();
		expect(mockWasmPbkdf2).toHaveBeenCalledWith(
			expect.objectContaining({
				iterations: 2500,
				hashLength: 32,
				outputType: "binary",
			}),
		);
	});

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

		await waitFor(() =>
			expect(mockSolveChallenge).toHaveBeenCalledTimes(1),
		);
		await waitFor(() =>
			expect(screen.getByText("Verified")).toBeInTheDocument(),
		);
		const payload = onPayloadChange.mock.calls.at(-1)?.[0] as string;
		expect(JSON.parse(globalThis.atob(payload))).toEqual({
			challenge,
			solution,
		});
	});

	it("shows a retry action when the challenge cannot load", async () => {
		mockAuthFetch.mockResolvedValueOnce(jsonResponse({}, false));
		renderWithProviders(
			<FormCaptcha formId="form-1" onPayloadChange={vi.fn()} />,
		);

		expect(
			await screen.findByText("Verification could not be loaded."),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Try again" }),
		).toBeInTheDocument();
	});
});
