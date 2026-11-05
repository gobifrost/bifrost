/**
 * Authenticated Playwright fixtures.
 *
 * Authenticated projects declare `metadata.authUser`; each test receives a
 * newly logged-in browser context and each `api` consumer receives its own
 * cookie jar. This intentionally avoids reusing a one-use refresh family from
 * global storage state.
 */

import {
	test as base,
	expect,
	type APIRequestContext,
	type APIResponse,
	type Browser,
	type BrowserContext,
	type Locator,
	type Page,
	type Response,
	request,
} from "@playwright/test";
import {
	createFreshApiContext,
	authenticateHumanBrowserContext,
	type HumanAuthUser,
} from "./human-auth";

export type AuthedApi = APIRequestContext;

function projectAuthUser(testInfo: {
	project: { metadata: Record<string, unknown> };
}): HumanAuthUser | undefined {
	const authUser = testInfo.project.metadata.authUser;
	if (
		authUser === "platform_admin" ||
		authUser === "org1_user" ||
		authUser === "org2_user"
	) {
		return authUser;
	}
	return undefined;
}

export const test = base.extend<{ api: AuthedApi }>({
	context: async ({ context }, provide, testInfo) => {
		const authUser = projectAuthUser(testInfo);
		if (authUser) await authenticateHumanBrowserContext(context, authUser);
		await provide(context);
	},
	api: async ({ browser }, provide, testInfo) => {
		const authUser = projectAuthUser(testInfo);
		if (!authUser) {
			throw new Error(
				"The api fixture requires project metadata.authUser; unauthenticated projects must use request explicitly.",
			);
		}
		const { context, api } = await createFreshApiContext(browser, authUser);
		try {
			await provide(api);
		} finally {
			await context.close();
		}
	},
});

export async function grantWorkspaceAppPolicy(
	api: AuthedApi,
	slug: string,
): Promise<void> {
	const prefix = `apps/${slug}`;
	const response = await api.put(
		`/api/files/policies/${encodeURIComponent(prefix)}`,
		{
			params: { location: "workspace" },
			data: {
				policies: {
					policies: [
						{
							name: "e2e_admin_app_source",
							actions: ["read", "write", "delete", "list"],
							when: { user: "is_platform_admin" },
						},
					],
				},
			},
		},
	);
	if (!response.ok()) {
		throw new Error(
			`grantWorkspaceAppPolicy(${slug}) failed: ${response.status()} ${await response.text()}`,
		);
	}
}

export async function publishAppAndWait(
	api: AuthedApi,
	appId: string,
	message?: string,
	timeoutMs = 2 * 60 * 1000,
): Promise<Record<string, unknown>> {
	const enqueue = await api.post(`/api/applications/${appId}/publish`, {
		data: { message: message ?? null },
	});
	if (!enqueue.ok()) {
		throw new Error(
			`enqueue application publish failed: ${enqueue.status()} ${await enqueue.text()}`,
		);
	}
	const accepted = (await enqueue.json()) as { job_id: string };
	const startedAt = Date.now();
	for (;;) {
		const response = await api.get(`/api/platform-jobs/${accepted.job_id}`);
		if (!response.ok()) {
			throw new Error(
				`read application publish ${accepted.job_id} failed: ${response.status()} ${await response.text()}`,
			);
		}
		const job = (await response.json()) as Record<string, unknown> & {
			status: string;
			error?: { message?: string } | null;
		};
		if (job.status === "succeeded") return job;
		if (job.status === "failed" || job.status === "cancelled") {
			throw new Error(
				job.error?.message || `Application publish ${appId} failed`,
			);
		}
		if (Date.now() - startedAt >= timeoutMs) {
			throw new Error(
				`Application publish ${appId} remained ${job.status} after ${timeoutMs}ms`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
}

export {
	expect,
	request,
	type APIRequestContext,
	type APIResponse,
	type Browser,
	type BrowserContext,
	type Locator,
	type Page,
	type Response,
};
