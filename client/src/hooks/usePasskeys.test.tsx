import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { supportsPasskeys, supportsPasskeyAutofill } from "@/services/passkeys";
import { usePasskeySupport } from "./usePasskeys";

vi.mock("@/services/passkeys", () => ({
	supportsPasskeys: vi.fn(),
	supportsPasskeyAutofill: vi.fn(),
}));

beforeEach(() => {
	vi.mocked(supportsPasskeys).mockReset().mockReturnValue(true);
	vi.mocked(supportsPasskeyAutofill).mockReset().mockResolvedValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe("usePasskeySupport", () => {
	it("preserves manual passkey support when autofill probing rejects", async () => {
		const error = new Error("Autofill probe failed");
		vi.mocked(supportsPasskeyAutofill).mockRejectedValue(error);
		const log = vi
			.spyOn(console, "error")
			.mockImplementation(() => undefined);
		const { result } = renderHook(() => usePasskeySupport());
		await waitFor(() =>
			expect(result.current).toEqual({
				supported: true,
				autofillSupported: false,
				isLoading: false,
			}),
		);
		expect(log).toHaveBeenCalledWith(
			"[usePasskeys] Capability check failed:",
			error,
		);
	});
	it("finishes loading when the WebAuthn support probe throws", async () => {
		vi.mocked(supportsPasskeys).mockImplementation(() => {
			throw new Error("Probe failed");
		});
		vi.spyOn(console, "error").mockImplementation(() => undefined);
		const { result } = renderHook(() => usePasskeySupport());
		await waitFor(() =>
			expect(result.current).toEqual({
				supported: false,
				autofillSupported: false,
				isLoading: false,
			}),
		);
		expect(supportsPasskeyAutofill).not.toHaveBeenCalled();
	});
	it("reports successful capability probes", async () => {
		const { result } = renderHook(() => usePasskeySupport());
		await waitFor(() =>
			expect(result.current).toEqual({
				supported: true,
				autofillSupported: true,
				isLoading: false,
			}),
		);
	});
});
