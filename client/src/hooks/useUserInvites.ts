import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
	regenerateInvite,
	resendInvite,
	revokeInvite,
} from "@/services/user-invites";

const USERS_QUERY_KEYS: ReadonlyArray<string> = ["users", "/api/users"];

function invalidateUsers(qc: ReturnType<typeof useQueryClient>) {
	for (const key of USERS_QUERY_KEYS) {
		qc.invalidateQueries({ queryKey: [key] });
	}
}

export function useResendInvite() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (userId: string) => resendInvite(userId),
		onSuccess: () => invalidateUsers(qc),
	});
}

export function useRegenerateInvite() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (userId: string) => regenerateInvite(userId),
		onSuccess: () => invalidateUsers(qc),
	});
}

export function useRevokeInvite() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (userId: string) => revokeInvite(userId),
		onSuccess: () => invalidateUsers(qc),
	});
}
