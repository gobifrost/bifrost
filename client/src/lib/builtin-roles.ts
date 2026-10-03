/** Fixed id of the built-in Platform Admin role (`shared/builtin_roles.py`). */
export const PLATFORM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000005";

/**
 * Built-in roles whose permission nothing checks yet, by role id. They are
 * already assigned, and take effect once the permission is enforced.
 */
export const NOT_ENFORCED_ROLE_NOTES: Record<string, string> = {
	// Secrets Reader
	"00000000-0000-0000-0000-000000000008":
		"Takes effect when secret decryption is enforced.",
};
