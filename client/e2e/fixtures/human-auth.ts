import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type {
	APIRequestContext,
	APIResponse,
	Browser,
	BrowserContext,
} from "@playwright/test";
import {
	isRefreshRequiredChallenge,
	REQUEST_REPLAY_BUDGET,
} from "../../src/lib/app-sdk/transport";
import { generateTOTP } from "../setup/totp";
import type { UserCredentials } from "./users";

const API_URL = process.env.TEST_API_URL || "http://api:8000";
const BROWSER_URL = process.env.TEST_BASE_URL || "http://localhost:3000";

type RequestOptions = NonNullable<
	Parameters<APIRequestContext["fetch"]>[1]
>;

export type HumanAuthUser = "platform_admin" | "org1_user" | "org2_user";

type CredentialsFile = Record<HumanAuthUser, UserCredentials>;

export interface FreshApiContext {
	context: BrowserContext;
	api: APIRequestContext;
}

function credentialsFile(): CredentialsFile {
	return JSON.parse(
		readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
	) as CredentialsFile;
}

export function knownUserCredentials(user: HumanAuthUser): UserCredentials {
	return credentialsFile()[user];
}

function credentialsFor(
	user: HumanAuthUser | UserCredentials,
): UserCredentials {
	return typeof user === "string" ? knownUserCredentials(user) : user;
}

async function csrfHeader(
	context: BrowserContext,
): Promise<Record<string, string>> {
	const csrf = (await context.cookies()).find(
		(cookie) => cookie.name === "csrf_token",
	);
	return csrf ? { "X-CSRF-Token": csrf.value } : {};
}

async function credentialGeneration(context: BrowserContext): Promise<string> {
	return (await context.cookies())
		.filter(
			(cookie) =>
				cookie.name === "refresh_token" ||
				cookie.name === "access_token" ||
				cookie.name.startsWith("access_token."),
		)
		.sort((left, right) => left.name.localeCompare(right.name))
		.map((cookie) => `${cookie.name}=${cookie.value}`)
		.join(";");
}

function requiresCsrf(method: string): boolean {
	return ["POST", "PUT", "PATCH", "DELETE"].includes(method);
}

function isRefreshRequiredApiChallenge(response: APIResponse): boolean {
	return isRefreshRequiredChallenge({
		status: response.status(),
		headers: new Headers(response.headers()),
	});
}

async function assertOk(response: APIResponse, operation: string): Promise<void> {
	if (!response.ok()) {
		throw new Error(
			`${operation} failed: ${response.status()} ${await response.text()}`,
		);
	}
}

/** Authenticate an existing seeded human against this context's cookie jar. */
export async function loginHumanApi(
	request: APIRequestContext,
	credentials: UserCredentials,
): Promise<void> {
	const login = await request.post("/auth/login", {
		form: {
			username: credentials.email,
			password: credentials.password,
		},
	});
	await assertOk(login, `login ${credentials.email}`);
	const loginBody = (await login.json()) as {
		mfa_required?: boolean;
		mfa_token?: string;
	};
	if (!loginBody.mfa_required) return;
	if (!loginBody.mfa_token) {
		throw new Error(`login ${credentials.email} omitted its MFA token`);
	}
	const mfa = await request.post("/auth/mfa/login", {
		data: {
			mfa_token: loginBody.mfa_token,
			code: generateTOTP(credentials.totpSecret),
		},
	});
	await assertOk(mfa, `MFA login ${credentials.email}`);
}

/**
 * Wrap an API cookie jar with the production browser refresh contract.
 * The raw request context is intentionally retained for login and refresh so
 * those endpoints cannot recurse through challenge handling.
 */
export function createChallengeAwareApi(
	request: APIRequestContext,
	context: BrowserContext,
): APIRequestContext {
	let refreshInFlight: Promise<boolean> | null = null;

	const refresh = async (observedGeneration: string): Promise<boolean> => {
		const currentGeneration = await credentialGeneration(context);
		if (currentGeneration !== observedGeneration) return currentGeneration !== "";
		if (refreshInFlight) return refreshInFlight;

		refreshInFlight = (async () => {
			const current = await credentialGeneration(context);
			if (current !== observedGeneration) return current !== "";
			const response = await request.fetch("/auth/refresh", {
				method: "POST",
				headers: await csrfHeader(context),
			});
			return response.ok();
		})().finally(() => {
			refreshInFlight = null;
		});
		return refreshInFlight;
	};

	const send = async (
		url: string,
		options: RequestOptions = {},
	): Promise<APIResponse> => {
		const method = options.method?.toUpperCase() || "GET";
		let attemptedGeneration = await credentialGeneration(context);
		const headers = {
			...(requiresCsrf(method) ? await csrfHeader(context) : {}),
			...(options.headers ?? {}),
		};
		let response = await request.fetch(url, { ...options, method, headers });
		let remaining = REQUEST_REPLAY_BUDGET;
		while (remaining > 0 && isRefreshRequiredApiChallenge(response)) {
			remaining -= 1;
			if (!(await refresh(attemptedGeneration))) return response;
			attemptedGeneration = await credentialGeneration(context);
			response = await request.fetch(url, {
				...options,
				method,
				headers: {
					...(requiresCsrf(method) ? await csrfHeader(context) : {}),
					...(options.headers ?? {}),
				},
			});
		}
		return response;
	};

	return new Proxy(request, {
		get(target, property, receiver) {
			if (property === "fetch") return send;
			if (typeof property === "string") {
				const method = property.toUpperCase();
				if (["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) {
					return (url: string, options: RequestOptions = {}) =>
						send(url, { ...options, method });
				}
			}
			const value = Reflect.get(target, property, receiver);
			return typeof value === "function" ? value.bind(target) : value;
		},
	}) as APIRequestContext;
}

/** Create a distinct, cookie-authenticated API session for one test consumer. */
export async function createFreshApiContext(
	browser: Browser,
	user: HumanAuthUser | UserCredentials,
): Promise<FreshApiContext> {
	const context = await browser.newContext({ baseURL: API_URL });
	try {
		await loginHumanApi(context.request, credentialsFor(user));
	} catch (error) {
		await context.close();
		throw error;
	}
	return { context, api: createChallengeAwareApi(context.request, context) };
}

/** Create a distinct browser session through the real sign-in and MFA page. */
export async function createFreshBrowserContext(
	browser: Browser,
	user: HumanAuthUser | UserCredentials,
): Promise<BrowserContext> {
	const context = await browser.newContext({ baseURL: BROWSER_URL });
	try {
		await authenticateHumanBrowserContext(context, user);
	} catch (error) {
		await context.close();
		throw error;
	}
	return context;
}

/** Log a real human into an existing browser context without altering its options. */
export async function authenticateHumanBrowserContext(
	context: BrowserContext,
	user: HumanAuthUser | UserCredentials,
): Promise<void> {
	const { authenticateInBrowser } = await import("../setup/auth-helpers");
	const page = await context.newPage();
	try {
		await authenticateInBrowser(page, credentialsFor(user));
	} finally {
		await page.close();
	}
}
