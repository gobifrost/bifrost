import { expect, type AuthedApi } from "./api-fixture";
import type { APIResponse } from "@playwright/test";

type ApiList<T> =
	T[] | { items?: T[]; resources?: T[]; collections?: T[]; total?: number };

type NamedResource = { id: string; name: string };
type Organization = NamedResource & { domain?: string };
type IntegrationMapping = {
	id: string;
	organization_id: string | null;
	entity_id: string;
	entity_name?: string | null;
};
type IntegrationDetail = NamedResource & { mappings?: IntegrationMapping[] };
type Workflow = NamedResource & {
	path?: string | null;
	function_name?: string | null;
};
type EventSource = NamedResource & { subscription_count?: number };
type EventSubscription = {
	id: string;
	target_type: string;
	workflow_id: string | null;
	event_type: string | null;
};
type Form = NamedResource & { workflow_id?: string | null; href?: string };
type Agent = NamedResource;
type HomeResource = {
	key: string;
	id: string;
	kind: "app" | "form" | "agent";
	name: string;
	href: string;
};
type HomeCollection = {
	id: string;
	name: string;
	resource_keys: string[];
};
type HomeResponse = {
	resources: HomeResource[];
	collections: HomeCollection[];
};

export type ReviewPack = {
	namespace: string;
	names: {
		organization: string;
		integration: string;
		mappingEntity: string;
		eventWorkflow: string;
		formWorkflow: string;
		eventSource: string;
		eventType: string;
		form: string;
		agent: string;
		collection: string;
		formMarker: string;
		eventMarker: string;
	};
	ids: {
		organizationId: string;
		integrationId: string;
		mappingId: string;
		eventWorkflowId: string;
		formWorkflowId: string;
		eventSourceId: string;
		eventSubscriptionId: string;
		formId: string;
		agentId: string;
		collectionId: string;
		formResourceKey: string;
		agentResourceKey: string;
	};
	index: {
		integrationUrl: string;
		eventSourceUrl: string;
		webhookPath: string;
		webhookPayload: Record<string, string>;
		homeUrl: string;
		formUrl: string;
		agentUrl: string;
		expected: {
			mappingEntity: string;
			formResultMarker: string;
			eventResultMarker: string;
		};
	};
	cleanup: () => Promise<void>;
};

function items<T>(
	body: ApiList<T>,
	key: "items" | "resources" | "collections" = "items",
): T[] {
	if (Array.isArray(body)) return body;
	const value = body[key];
	return Array.isArray(value) ? value : [];
}

async function expectOk(response: APIResponse): Promise<void> {
	expect(response.ok(), await response.text()).toBe(true);
}

async function deleteOwned(
	responsePromise: Promise<APIResponse>,
): Promise<void> {
	const response = await responsePromise;
	expect([200, 204, 404]).toContain(response.status());
}

function slug(namespace: string): string {
	return namespace
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
}

function pythonIdentifier(namespace: string): string {
	const identifier = namespace
		.toLowerCase()
		.replace(/[^a-z0-9_]+/g, "_")
		.replace(/^_+|_+$/g, "");
	return /^[a-z_]/.test(identifier) ? identifier : `n_${identifier}`;
}

function pagedUrl(path: string, limit: number, offset: number): string {
	const separator = path.includes("?") ? "&" : "?";
	return `${path}${separator}limit=${limit}&offset=${offset}`;
}

async function listAll<T>(api: AuthedApi, path: string): Promise<T[]> {
	const first = await api.get(pagedUrl(path, 1000, 0));
	await expectOk(first);
	const firstBody = (await first.json()) as ApiList<T>;
	const firstItems = items<T>(firstBody);
	if (
		Array.isArray(firstBody) ||
		typeof firstBody.total !== "number" ||
		firstItems.length >= firstBody.total
	) {
		return firstItems;
	}

	const all = [...firstItems];
	while (all.length < firstBody.total) {
		const response = await api.get(pagedUrl(path, 1000, all.length));
		await expectOk(response);
		const pageItems = items<T>((await response.json()) as ApiList<T>);
		if (pageItems.length === 0) {
			throw new Error(
				`Endpoint ${path} reported ${firstBody.total} items but returned an empty page at offset ${all.length}`,
			);
		}
		all.push(...pageItems);
	}
	return all;
}

async function listByName<T extends NamedResource>(
	api: AuthedApi,
	path: string,
	name: string,
): Promise<T | undefined> {
	const resources = await listAll<T>(api, path);
	return resources.find((item) => item.name === name);
}

async function ensureOrganization(
	api: AuthedApi,
	name: string,
	namespace: string,
): Promise<Organization> {
	const existing = await listByName<Organization>(
		api,
		"/api/organizations",
		name,
	);
	if (existing) return existing;
	const response = await api.post("/api/organizations", {
		data: {
			name,
			domain: `${slug(namespace)}.review.gobifrost.dev`,
		},
	});
	await expectOk(response);
	return (await response.json()) as Organization;
}

async function ensureIntegration(
	api: AuthedApi,
	name: string,
): Promise<IntegrationDetail> {
	const existing = await listByName<IntegrationDetail>(
		api,
		"/api/integrations",
		name,
	);
	if (existing) {
		const detail = await api.get(`/api/integrations/${existing.id}`);
		await expectOk(detail);
		return (await detail.json()) as IntegrationDetail;
	}
	const response = await api.post("/api/integrations", { data: { name } });
	await expectOk(response);
	return (await response.json()) as IntegrationDetail;
}

async function ensureMapping(
	api: AuthedApi,
	integrationId: string,
	organizationId: string,
	entityId: string,
): Promise<IntegrationMapping> {
	const detailResponse = await api.get(`/api/integrations/${integrationId}`);
	await expectOk(detailResponse);
	const detail = (await detailResponse.json()) as IntegrationDetail;
	const existing = (detail.mappings ?? []).find(
		(mapping) => mapping.organization_id === organizationId,
	);
	if (existing?.entity_id === entityId && existing.entity_name === entityId)
		return existing;

	if (existing) {
		const update = await api.post(
			`/api/integrations/${integrationId}/mappings/batch`,
			{
				data: {
					mappings: [
						{
							organization_id: organizationId,
							entity_id: entityId,
							entity_name: entityId,
						},
					],
				},
			},
		);
		await expectOk(update);
	} else {
		const create = await api.post(
			`/api/integrations/${integrationId}/mappings`,
			{
				data: {
					organization_id: organizationId,
					entity_id: entityId,
					entity_name: entityId,
				},
			},
		);
		await expectOk(create);
	}

	const refreshed = await api.get(`/api/integrations/${integrationId}`);
	await expectOk(refreshed);
	const refreshedDetail = (await refreshed.json()) as IntegrationDetail;
	const mapping = (refreshedDetail.mappings ?? []).find(
		(candidate) => candidate.organization_id === organizationId,
	);
	expect(mapping).toMatchObject({
		entity_id: entityId,
		entity_name: entityId,
	});
	return mapping!;
}

// A webhook delivery runs unattended as the Global Default Identity, whose
// reach is Global only, so it cannot read the review organization's mapping.
// The form workflow, run by the signed-in admin, proves the mapping read.
function eventWorkflowSource(
	workflowName: string,
	resultMarker: string,
): string {
	return `from bifrost import workflow\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}(event_type: str = "", marker: str = "", _event: dict | None = None) -> dict:\n    return {\n        "event_type": event_type,\n        "marker": marker,\n        "result_marker": "${resultMarker}",\n        "event_id": (_event or {}).get("id"),\n    }\n`;
}

function formWorkflowSource(
	workflowName: string,
	integrationName: string,
	resultMarker: string,
): string {
	return `from bifrost import workflow, integrations\n\n@workflow(name="${workflowName}")\nasync def ${workflowName}(review_note: str) -> dict:\n    mappings = await integrations.list_mappings("${integrationName}", scope="global")\n    return {\n        "review_note": review_note,\n        "result_marker": "${resultMarker}",\n        "mapping_entity_ids": [mapping.entity_id for mapping in (mappings or [])],\n    }\n`;
}

async function ensureWorkflow(
	api: AuthedApi,
	path: string,
	functionName: string,
	content: string,
): Promise<Workflow> {
	const write = await api.put("/api/files/editor/content", {
		data: { path, content, encoding: "utf-8" },
	});
	await expectOk(write);

	const workflows = await listAll<Workflow>(api, "/api/workflows");
	const workflow = workflows.find(
		(candidate) =>
			candidate.name === functionName ||
			(candidate.path === path &&
				candidate.function_name === functionName),
	);
	if (workflow) return workflow;

	const register = await api.post("/api/workflows/register", {
		data: {
			path,
			function_name: functionName,
			organization_id: null,
			access_level: "authenticated",
		},
	});
	await expectOk(register);
	return (await register.json()) as Workflow;
}

async function ensureEventSource(
	api: AuthedApi,
	name: string,
): Promise<EventSource> {
	const existing = await listByName<EventSource>(
		api,
		"/api/events/sources?source_type=webhook",
		name,
	);
	if (existing) return existing;
	const response = await api.post("/api/events/sources", {
		data: {
			name,
			source_type: "webhook",
			organization_id: null,
			webhook: {
				adapter_name: "generic",
				config: { event_type_field: "event_type" },
			},
		},
	});
	await expectOk(response);
	return (await response.json()) as EventSource;
}

async function ensureSubscription(
	api: AuthedApi,
	sourceId: string,
	workflowId: string,
	eventType: string,
): Promise<EventSubscription> {
	const list = await api.get(`/api/events/sources/${sourceId}/subscriptions`);
	await expectOk(list);
	const existing = items<EventSubscription>(
		(await list.json()) as ApiList<EventSubscription>,
	).find(
		(subscription) =>
			subscription.target_type === "workflow" &&
			subscription.workflow_id === workflowId &&
			subscription.event_type === eventType,
	);
	if (existing) return existing;
	const response = await api.post(
		`/api/events/sources/${sourceId}/subscriptions`,
		{
			data: {
				target_type: "workflow",
				workflow_id: workflowId,
				event_type: eventType,
			},
		},
	);
	await expectOk(response);
	return (await response.json()) as EventSubscription;
}

async function ensureForm(
	api: AuthedApi,
	name: string,
	workflowId: string,
): Promise<Form> {
	const existing = await listByName<Form>(api, "/api/forms", name);
	const data = {
		name,
		description: "Review fixture form owned by e2e review pack",
		workflow_id: workflowId,
		form_schema: {
			fields: [
				{
					name: "review_note",
					label: "Review Note",
					type: "text",
					required: true,
				},
			],
		},
		access_level: "authenticated",
	};
	if (existing) {
		if (existing.workflow_id === workflowId) return existing;
		const update = await api.patch(`/api/forms/${existing.id}`, { data });
		await expectOk(update);
		return (await update.json()) as Form;
	}
	const response = await api.post("/api/forms", { data });
	await expectOk(response);
	return (await response.json()) as Form;
}

async function ensureAgent(api: AuthedApi, name: string): Promise<Agent> {
	const existing = await listByName<Agent>(api, "/api/agents", name);
	if (existing) return existing;
	const response = await api.post("/api/agents", {
		data: {
			name,
			description: "Review fixture agent owned by e2e review pack",
			system_prompt:
				"You are a deterministic review fixture agent. Do not call external providers.",
			access_level: "authenticated",
		},
	});
	await expectOk(response);
	return (await response.json()) as Agent;
}

async function readHome(api: AuthedApi): Promise<HomeResponse> {
	const response = await api.get("/api/home");
	await expectOk(response);
	return (await response.json()) as HomeResponse;
}

async function ensureHomeCollection(
	api: AuthedApi,
	name: string,
	resourceKeys: string[],
): Promise<HomeCollection> {
	const home = await readHome(api);
	const existing = home.collections.find(
		(collection) => collection.name === name,
	);
	const body = {
		name,
		description: "Review fixture collection owned by e2e review pack",
		icon: "folder",
		shared: false,
		organization_id: null,
		resource_keys: resourceKeys,
	};
	if (existing) {
		const sameKeys =
			existing.resource_keys.length === resourceKeys.length &&
			existing.resource_keys.every(
				(key, index) => key === resourceKeys[index],
			);
		if (sameKeys) return existing;
		const update = await api.put(`/api/home/collections/${existing.id}`, {
			data: body,
		});
		await expectOk(update);
		return (await update.json()) as HomeCollection;
	}
	const create = await api.post("/api/home/collections", { data: body });
	await expectOk(create);
	return (await create.json()) as HomeCollection;
}

async function cleanupReviewPackNamespace(
	api: AuthedApi,
	namespace: string,
): Promise<void> {
	const safeNamespace = slug(namespace);
	const identifier = pythonIdentifier(namespace);
	const formName = `E2E Review Form ${safeNamespace}`;
	const agentName = `E2E Review Agent ${safeNamespace}`;
	const collectionName = `E2E Review Collection ${safeNamespace}`;
	const sourceName = `E2E Review Webhook ${safeNamespace}`;
	const integrationName = `E2E Review Integration ${safeNamespace}`;
	const organizationName = `E2E Review Org ${safeNamespace}`;
	const failures: unknown[] = [];
	const attempt = async (operation: () => Promise<void>) => {
		try {
			await operation();
		} catch (error) {
			failures.push(error);
		}
	};

	const home = await readHome(api);
	const collection = home.collections.find(
		(candidate) => candidate.name === collectionName,
	);
	if (collection) {
		await attempt(() =>
			deleteOwned(api.delete(`/api/home/collections/${collection.id}`)),
		);
	}

	const source = await listByName<EventSource>(
		api,
		"/api/events/sources?source_type=webhook",
		sourceName,
	);
	if (source) {
		await attempt(() =>
			deleteOwned(api.delete(`/api/events/sources/${source.id}`)),
		);
	}

	const form = await listByName<Form>(api, "/api/forms", formName);
	if (form) {
		await attempt(() => deleteOwned(api.delete(`/api/forms/${form.id}`)));
	}

	const agent = await listByName<Agent>(api, "/api/agents", agentName);
	if (agent) {
		await attempt(() => deleteOwned(api.delete(`/api/agents/${agent.id}`)));
	}

	const integration = await listByName<IntegrationDetail>(
		api,
		"/api/integrations",
		integrationName,
	);
	if (integration) {
		await attempt(() =>
			deleteOwned(api.delete(`/api/integrations/${integration.id}`)),
		);
	}

	const organization = await listByName<Organization>(
		api,
		"/api/organizations",
		organizationName,
	);
	if (organization) {
		await attempt(() =>
			deleteOwned(api.delete(`/api/organizations/${organization.id}`)),
		);
	}

	await attempt(() =>
		deleteOwned(
			api.delete(
				`/api/files/editor?path=${encodeURIComponent(`e2e_review_event_${identifier}.py`)}`,
			),
		),
	);
	await attempt(() =>
		deleteOwned(
			api.delete(
				`/api/files/editor?path=${encodeURIComponent(`e2e_review_form_${identifier}.py`)}`,
			),
		),
	);

	if (failures.length > 0) {
		throw new AggregateError(
			failures,
			`Failed to clean review pack namespace ${safeNamespace}`,
		);
	}
}

export async function ensureReviewPack(
	api: AuthedApi,
	namespace: string,
	options: { cleanupOnFailure?: boolean } = {},
): Promise<ReviewPack> {
	const safeNamespace = slug(namespace);
	const identifier = pythonIdentifier(namespace);
	const eventResultMarker = `review-event-result-${safeNamespace}`;
	const names = {
		organization: `E2E Review Org ${safeNamespace}`,
		integration: `E2E Review Integration ${safeNamespace}`,
		mappingEntity: `review-tenant-${safeNamespace}`,
		eventWorkflow: `e2e_review_event_${identifier}`,
		formWorkflow: `e2e_review_form_${identifier}`,
		eventSource: `E2E Review Webhook ${safeNamespace}`,
		eventType: `review.fixture.${safeNamespace}`,
		form: `E2E Review Form ${safeNamespace}`,
		agent: `E2E Review Agent ${safeNamespace}`,
		collection: `E2E Review Collection ${safeNamespace}`,
		formMarker: `review-form-result-${safeNamespace}`,
		eventMarker: `review-webhook-${safeNamespace}`,
	};

	try {
		const organization = await ensureOrganization(
			api,
			names.organization,
			safeNamespace,
		);
		const integration = await ensureIntegration(api, names.integration);
		const mapping = await ensureMapping(
			api,
			integration.id,
			organization.id,
			names.mappingEntity,
		);
		const eventWorkflow = await ensureWorkflow(
			api,
			`e2e_review_event_${identifier}.py`,
			names.eventWorkflow,
			eventWorkflowSource(names.eventWorkflow, eventResultMarker),
		);
		const formWorkflow = await ensureWorkflow(
			api,
			`e2e_review_form_${identifier}.py`,
			names.formWorkflow,
			formWorkflowSource(
				names.formWorkflow,
				names.integration,
				names.formMarker,
			),
		);
		const eventSource = await ensureEventSource(api, names.eventSource);
		const subscription = await ensureSubscription(
			api,
			eventSource.id,
			eventWorkflow.id,
			names.eventType,
		);
		const form = await ensureForm(api, names.form, formWorkflow.id);
		const agent = await ensureAgent(api, names.agent);
		const home = await readHome(api);
		const formResource = home.resources.find(
			(resource) => resource.kind === "form" && resource.id === form.id,
		);
		const agentResource = home.resources.find(
			(resource) => resource.kind === "agent" && resource.id === agent.id,
		);
		expect(
			formResource,
			`Home resource missing for form ${form.id}`,
		).toBeTruthy();
		expect(
			agentResource,
			`Home resource missing for agent ${agent.id}`,
		).toBeTruthy();
		const collection = await ensureHomeCollection(api, names.collection, [
			formResource!.key,
			agentResource!.key,
		]);

		return {
			namespace: safeNamespace,
			names,
			ids: {
				organizationId: organization.id,
				integrationId: integration.id,
				mappingId: mapping.id,
				eventWorkflowId: eventWorkflow.id,
				formWorkflowId: formWorkflow.id,
				eventSourceId: eventSource.id,
				eventSubscriptionId: subscription.id,
				formId: form.id,
				agentId: agent.id,
				collectionId: collection.id,
				formResourceKey: formResource!.key,
				agentResourceKey: agentResource!.key,
			},
			index: {
				integrationUrl: `/integrations/${integration.id}`,
				eventSourceUrl: `/event-sources/${eventSource.id}`,
				webhookPath: `/api/hooks/${eventSource.id}`,
				webhookPayload: {
					event_type: names.eventType,
					marker: names.eventMarker,
				},
				homeUrl: "/",
				formUrl: formResource!.href || `/execute/${form.id}`,
				agentUrl: agentResource!.href,
				expected: {
					mappingEntity: names.mappingEntity,
					formResultMarker: names.formMarker,
					eventResultMarker,
				},
			},
			cleanup: () => cleanupReviewPackNamespace(api, namespace),
		};
	} catch (error) {
		if (options.cleanupOnFailure !== false) {
			await cleanupReviewPackNamespace(api, namespace);
		}
		throw error;
	}
}
