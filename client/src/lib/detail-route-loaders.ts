import type { LoaderFunctionArgs } from "react-router-dom";

import { prepareAppBundle } from "@/components/jsx-app/BundledAppShell";
import { $api } from "@/lib/api-client";
import { queryClient } from "@/lib/queryClient";
import type { ApplicationPublic } from "@/hooks/useApplications";

export const DETAIL_STALE_TIME_MS = 60_000;

export function agentDetailQueryOptions(agentId: string) {
	return $api.queryOptions(
		"get",
		"/api/agents/{agent_id}",
		{ params: { path: { agent_id: agentId } } },
		{ staleTime: DETAIL_STALE_TIME_MS },
	);
}

export function applicationDetailQueryOptions(slug: string) {
	return $api.queryOptions(
		"get",
		"/api/applications/{slug}",
		{ params: { path: { slug } } },
		{ staleTime: DETAIL_STALE_TIME_MS },
	);
}

async function decodeImage(src: string | null | undefined): Promise<void> {
	if (!src) return;

	await new Promise<void>((resolve) => {
		const image = new Image();
		image.onload = () => resolve();
		image.onerror = () => resolve();
		image.src = src;
		if (image.complete) resolve();
	});
}

export async function agentDetailLoader({ params }: LoaderFunctionArgs) {
	const agentId = params.id;
	if (!agentId) return null;

	const agent = await queryClient.ensureQueryData(
		agentDetailQueryOptions(agentId),
	);
	await decodeImage(agent.logo_url);
	return null;
}

export function prefetchAgentDetail(agentId: string): void {
	void queryClient
		.ensureQueryData(agentDetailQueryOptions(agentId))
		.then((agent) => decodeImage(agent.logo_url))
		.catch(() => undefined);
}

export function prefetchApplicationDetail(
	application: ApplicationPublic,
	preview: boolean,
): void {
	queryClient.setQueryData(
		applicationDetailQueryOptions(application.slug).queryKey,
		application,
	);
	void Promise.all([
		decodeImage(application.logo_url),
		prepareAppBundle({ appId: application.id, isPreview: preview }),
	]).catch(() => undefined);
}

export function applicationDetailLoader(preview: boolean) {
	return async ({ params, request }: LoaderFunctionArgs) => {
		const slug = params.applicationId;
		if (!slug) return null;

		const application = await queryClient.ensureQueryData(
			applicationDetailQueryOptions(slug),
		);
		await Promise.all([
			decodeImage(application.logo_url),
			prepareAppBundle({
				appId: application.id,
				isPreview: preview,
				signal: request.signal,
			}),
		]);
		return null;
	};
}
