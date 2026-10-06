import type { ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/services/user-invites", () => ({
	resendInvite: vi.fn().mockResolvedValue({ event_emitted: true }),
	regenerateInvite: vi.fn(),
	revokeInvite: vi.fn().mockResolvedValue(undefined),
	sendInviteEmail: vi.fn(),
}));

import { useResendInvite, useRevokeInvite } from "./useUserInvites";

function setup() {
	const queryClient = new QueryClient();
	const invalidate = vi.spyOn(queryClient, "invalidateQueries");
	const wrapper = ({ children }: { children: ReactNode }) => (
		<QueryClientProvider client={queryClient}>
			{children}
		</QueryClientProvider>
	);
	return { invalidate, wrapper };
}

describe("useUserInvites", () => {
	it.each([
		["revoking", useRevokeInvite],
		["resending", useResendInvite],
	])(
		"refreshes the users list and the person's page after %s an invite",
		async (_, useInvite) => {
			const { invalidate, wrapper } = setup();
			const { result } = renderHook(() => useInvite(), { wrapper });

			await act(async () => {
				await result.current.mutateAsync("user-1");
			});

			expect(invalidate).toHaveBeenCalledWith({
				queryKey: ["get", "/api/users"],
			});
			expect(invalidate).toHaveBeenCalledWith({
				queryKey: ["get", "/api/users/{user_id}"],
			});
		},
	);
});
