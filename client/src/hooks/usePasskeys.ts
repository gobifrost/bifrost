/**
 * React hooks for passkey management
 *
 * Provides hooks for:
 * - Checking browser support
 * - Registering passkeys
 * - Listing/deleting passkeys
 * - Authenticating with passkeys
 */

import { useState, useEffect } from "react";
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
	supportsPasskeys,
	supportsPasskeyAutofill,
	registerPasskey,
	authenticateWithPasskey,
	getPasskeys,
	deletePasskey,
	type LoginTokens,
} from "@/services/passkeys";

// =============================================================================
// Feature Detection Hook
// =============================================================================

export interface PasskeySupportStatus {
	supported: boolean;
	autofillSupported: boolean;
	isLoading: boolean;
}

/**
 * Hook to check if the browser supports passkeys
 */
export function usePasskeySupport(): PasskeySupportStatus {
	const [status, setStatus] = useState<PasskeySupportStatus>({
		supported: false,
		autofillSupported: false,
		isLoading: true,
	});

	useEffect(() => {
		async function checkSupport() {
			let supported = false;
			try {
				supported = supportsPasskeys();
				const autofillSupported = supported
					? await supportsPasskeyAutofill()
					: false;
				setStatus({
					supported,
					autofillSupported,
					isLoading: false,
				});
			} catch (error) {
				console.error("[usePasskeys] Capability check failed:", error);
				setStatus({ supported, autofillSupported: false, isLoading: false });
			}
		}
		void checkSupport();
	}, []);

	return status;
}

// =============================================================================
// Passkey List Hook
// =============================================================================

const PASSKEYS_QUERY_KEY = ["passkeys"];

/**
 * Hook to fetch and manage user's passkeys
 */
export function usePasskeyList() {
	return useQuery({
		queryKey: PASSKEYS_QUERY_KEY,
		queryFn: getPasskeys,
		staleTime: 30000, // 30 seconds
	});
}

// =============================================================================
// Register Passkey Hook
// =============================================================================

interface RegisterPasskeyOptions {
	/** Disable when the caller renders persistent error feedback. */
	showErrorToast?: boolean;
	onSuccess?: (passkey: { passkey_id: string; name: string }) => void;
	onError?: (error: Error) => void;
}

/**
 * Hook to register a new passkey
 */
export function useRegisterPasskey(options: RegisterPasskeyOptions = {}) {
	const queryClient = useQueryClient();

	return useMutation({
		mutationFn: async (deviceName?: string) => {
			return registerPasskey(deviceName);
		},
		onSuccess: (result) => {
			queryClient.invalidateQueries({ queryKey: PASSKEYS_QUERY_KEY });
			toast.success("Passkey registered", {
				description: `"${result.name}" has been added to your account`,
			});
			options.onSuccess?.({
				passkey_id: result.passkey_id,
				name: result.name,
			});
		},
		onError: (error: Error) => {
			if (options.showErrorToast !== false) {
				toast.error("Failed to register passkey", {
					description: error.message,
				});
			}
			options.onError?.(error);
		},
	});
}

// =============================================================================
// Delete Passkey Hook
// =============================================================================

interface DeletePasskeyOptions {
	/** Disable when the caller renders persistent error feedback. */
	showErrorToast?: boolean;
	onSuccess?: () => void;
	onError?: (error: Error) => void;
}

/**
 * Hook to delete a passkey
 */
export function useDeletePasskey(options: DeletePasskeyOptions = {}) {
	const queryClient = useQueryClient();

	return useMutation({
		mutationFn: deletePasskey,
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: PASSKEYS_QUERY_KEY });
			toast.success("Passkey removed", {
				description: "The passkey has been removed from your account",
			});
			options.onSuccess?.();
		},
		onError: (error: Error) => {
			if (options.showErrorToast !== false) {
				toast.error("Failed to remove passkey", {
					description: error.message,
				});
			}
			options.onError?.(error);
		},
	});
}

// =============================================================================
// Authenticate with Passkey Hook
// =============================================================================

interface AuthenticatePasskeyOptions {
	onSuccess?: (tokens: LoginTokens) => void;
	onError?: (error: Error) => void;
}

/**
 * Hook to authenticate with a passkey (for login page)
 */
export function useAuthenticateWithPasskey(
	options: AuthenticatePasskeyOptions = {},
) {
	return useMutation({
		mutationFn: async (email?: string) => {
			return authenticateWithPasskey(email);
		},
		onSuccess: (tokens) => {
			options.onSuccess?.(tokens);
		},
		onError: (error: Error) => {
			// Don't show toast for cancelled auth - that's expected user behavior
			if (
				!error.message.includes("cancelled") &&
				!error.message.includes("not allowed")
			) {
				toast.error("Passkey authentication failed", {
					description: error.message,
				});
			}
			options.onError?.(error);
		},
	});
}

// =============================================================================
// Convenience Hook for Passkey Management
// =============================================================================

/**
 * Combined hook for passkey management UI
 * Provides all passkey operations in one hook
 */
export function usePasskeys() {
	const support = usePasskeySupport();
	const listQuery = usePasskeyList();
	const registerMutation = useRegisterPasskey();
	const deleteMutation = useDeletePasskey();

	return {
		// Support status
		isSupported: support.supported,
		isAutofillSupported: support.autofillSupported,
		isSupportLoading: support.isLoading,

		// Passkey list
		passkeys: listQuery.data?.passkeys ?? [],
		passkeyCount: listQuery.data?.count ?? 0,
		isLoading: listQuery.isLoading,
		isFetching: listQuery.isFetching,
		isError: listQuery.isError,
		error: listQuery.error,
		refetch: listQuery.refetch,

		// Register
		register: registerMutation.mutate,
		registerAsync: registerMutation.mutateAsync,
		isRegistering: registerMutation.isPending,

		// Delete
		delete: deleteMutation.mutate,
		deleteAsync: deleteMutation.mutateAsync,
		isDeleting: deleteMutation.isPending,
	};
}
