/**
 * E2E User Fixtures
 *
 * Defines test users that mirror the backend E2E fixtures.
 * These users are created during global setup and used across all tests.
 */

/**
 * Test user definition
 */
export interface TestUser {
	/** Unique key for this user (used for storage state file naming) */
	key: string;
	/** User email address */
	email: string;
	/** User display name */
	name: string;
	/** User password */
	password: string;
	/** Whether this user is a platform superuser */
	isSuperuser: boolean;
	/** Organization domain (if org user) */
	orgDomain?: string;
}

/**
 * Runtime user credentials populated during global setup.
 * This is saved to .auth/credentials.json for use in tests.
 */
export interface UserCredentials {
	email: string;
	password: string;
	name: string;
	totpSecret: string;
	userId: string;
	organizationId?: string;
	accessToken: string;
	refreshToken: string;
	isSuperuser: boolean;
}

/**
 * Test users - mirrors backend E2E fixtures.
 *
 * platform_admin: First user to register, becomes superuser
 * org1_user: Regular user in org1 (Bifrost Dev Org)
 * org2_user: Regular user in org2 (Second Test Org) - for isolation tests
 */
export const USERS: Record<string, TestUser> = {
	platform_admin: {
		key: "platform_admin",
		email: "admin@gobifrost.com",
		name: "Platform Admin",
		password: "AdminPass123!",
		isSuperuser: true,
	},
	org1_user: {
		key: "org1_user",
		email: "alice@gobifrost.dev",
		name: "Alice Smith",
		password: "AlicePass123!",
		isSuperuser: false,
		orgDomain: "gobifrost.dev",
	},
	org2_user: {
		key: "org2_user",
		email: "bob@example.com",
		name: "Bob Jones",
		password: "BobPass123!",
		isSuperuser: false,
		orgDomain: "example.com",
	},
};

/**
 * Test organizations - created by platform_admin during setup.
 */
export const ORGANIZATIONS = {
	org1: {
		name: "Bifrost Dev Org",
		domain: "gobifrost.dev",
	},
	org2: {
		name: "Second Test Org",
		domain: "example.com",
	},
};

/** Directory holding persistent seeded-user credentials. */
export const AUTH_STATE_DIR = ".auth";

export function getCredentialsPath(): string {
	return `${AUTH_STATE_DIR}/credentials.json`;
}
