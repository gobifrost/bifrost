/**
 * SDK Service
 *
 * The Bifrost SDK download URL. Per-user "developer context" overrides
 * have been removed — the caller's effective org is the auth-verified
 * ``user.organization_id``; cross-org targeting happens via the explicit
 * ``scope`` parameter on each SDK call.
 */

// =============================================================================
// SDK Download
// =============================================================================

export function getSdkDownloadUrl(): string {
	return "/api/cli/download/bifrost-cli.tar.gz";
}

// =============================================================================
// Service Export
// =============================================================================

export const sdkService = {
	getSdkDownloadUrl,
};
