import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
	regenerateInvite,
	resendInvite,
	revokeInvite,
	sendInviteEmail,
} from "@/services/user-invites";

/** The users list and a person's page both show invite status. */
const USERS_QUERY_KEYS = [
	["get", "/api/users"],
	["get", "/api/users/{user_id}"],
];

function invalidateUsers(qc: ReturnType<typeof useQueryClient>) {
	for (const queryKey of USERS_QUERY_KEYS) {
		qc.invalidateQueries({ queryKey });
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

export function useSendInvite() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: ({
			userId,
			registrationUrl,
		}: {
			userId: string;
			registrationUrl: string;
		}) => sendInviteEmail(userId, registrationUrl),
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
