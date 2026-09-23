import { IntegrationReadError } from "@/components/integrations/IntegrationReadError";
import { failedMappings } from "@/components/integrations/mapping-save";
import { IntegrationPageHeader } from "@/components/integrations/IntegrationPageHeader";
import { useState, useEffect, useMemo, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { IntegrationDeleteDialog } from "@/components/integrations/IntegrationDeleteDialog";
import { toast } from "sonner";
import {
	useIntegration,
	useUpdateMapping,
	useDeleteMapping,
	useUpdateIntegrationConfig,
	useTestIntegration,
	useBatchUpsertMappings,
	useCreateMapping,
	useAuthorizeMapping,
	useDisconnectMapping,
	useRefreshMapping,
	planOrgConfigSave,
	type IntegrationTestResponse,
} from "@/services/integrations";
import { $api } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import {
	useAuthorizeOAuthConnection,
	useRefreshOAuthToken,
	useDeleteOAuthConnection,
} from "@/hooks/useOAuth";
import { isExpired, expiresSoon } from "@/lib/client-types";
import { CreateOAuthConnectionDialog } from "@/components/oauth/CreateOAuthConnectionDialog";
import { CreateIntegrationDialog } from "@/components/integrations/CreateIntegrationDialog";
import { OrgConfigDialog } from "@/components/integrations/OrgConfigDialog";
import { ConfigOverridesTab } from "@/components/integrations/ConfigOverridesTab";
import { useIntegrationEntities } from "@/hooks/useIntegrationEntities";
import { useAutoMatch } from "@/hooks/useAutoMatch";
import { GenerateSDKDialog } from "@/components/integrations/GenerateSDKDialog";
import { IntegrationOverview } from "@/components/integrations/IntegrationOverview";
import {
	IntegrationMappingsTab,
	type OrgWithMapping,
	type PendingMappingAction,
} from "@/components/integrations/IntegrationMappingsTab";
import { IntegrationTestPanel } from "@/components/integrations/IntegrationTestPanel";
import { IntegrationDefaultsDialog } from "@/components/integrations/IntegrationDefaultsDialog";

interface MappingFormData {
	organization_id: string;
	entity_id: string;
	entity_name: string;
	oauth_token_id?: string;
	config: Record<string, unknown>;
}

export function IntegrationDetail() {
	const { id: integrationId } = useParams<{ id: string }>();

	const [oauthConfigDialogOpen, setOAuthConfigDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [configDialogOpen, setConfigDialogOpen] = useState(false);
	const [defaultsDialogOpen, setDefaultsDialogOpen] = useState(false);
	const [defaultsError, setDefaultsError] = useState<string | null>(null);
	const [defaultsFormValues, setDefaultsFormValues] = useState<
		Record<string, unknown>
	>({});
	const [selectedOrgForConfig, setSelectedOrgForConfig] = useState<
		OrgWithMapping | undefined
	>();
	const [deleteMappingConfirm, setDeleteMappingConfirm] =
		useState<OrgWithMapping | null>(null);
	const [editingOAuthConfig, setEditingOAuthConfig] = useState(false);
	const [deleteOAuthDialogOpen, setDeleteOAuthDialogOpen] = useState(false);
	const [generateSDKDialogOpen, setGenerateSDKDialogOpen] = useState(false);
	const [testDialogOpen, setTestDialogOpen] = useState(false);
	const [testOrgId, setTestOrgId] = useState<string | null>(null);
	const [testEndpoint, setTestEndpoint] = useState<string>("/");
	const [testResult, setTestResult] =
		useState<IntegrationTestResponse | null>(null);

	const queryClient = useQueryClient();

	// Fetch integration details (includes mappings and OAuth config)
	const {
		data: integration,
		isLoading: isLoadingIntegration,
		isError: isIntegrationError,
		isFetching: isFetchingIntegration,
		refetch: refetchIntegration,
	} = useIntegration(integrationId || "");

	// Fetch organizations
	const {
		data: orgsData,
		isLoading: isLoadingOrgs,
		isError: isOrgsError,
		isFetching: isFetchingOrgs,
		refetch: refetchOrgs,
	} = $api.useQuery("get", "/api/organizations");

	const updateMutation = useUpdateMapping();
	const deleteMutation = useDeleteMapping();
	const updateConfigMutation = useUpdateIntegrationConfig();
	const authorizeMutation = useAuthorizeOAuthConnection();
	const refreshMutation = useRefreshOAuthToken();
	const deleteOAuthMutation = useDeleteOAuthConnection();
	const testMutation = useTestIntegration();
	const batchMutation = useBatchUpsertMappings();
	const createMappingMutation = useCreateMapping();
	const authorizeMappingMutation = useAuthorizeMapping();
	const disconnectMappingMutation = useDisconnectMapping();
	const refreshMappingMutation = useRefreshMapping();
	const [pendingMappingAction, setPendingMappingActionState] =
		useState<PendingMappingAction | null>(null);
	const pendingMappingActionRef = useRef<PendingMappingAction | null>(null);

	const setPendingMappingAction = (next: PendingMappingAction | null) => {
		pendingMappingActionRef.current = next;
		setPendingMappingActionState(next);
	};

	const clearPendingMappingAction = (expected: PendingMappingAction) => {
		const current = pendingMappingActionRef.current;
		if (
			current?.orgId === expected.orgId &&
			current.action === expected.action
		) {
			setPendingMappingAction(null);
		}
	};

	// Memoize to stabilize references for the useEffect that combines them
	const organizations = useMemo(
		() => (Array.isArray(orgsData) ? orgsData : []),
		[orgsData],
	);
	const mappings = useMemo(
		() => integration?.mappings || [],
		[integration?.mappings],
	);

	// Fetch entities from data provider
	const {
		data: entities = [],
		isLoading: isLoadingEntities,
		isError: isEntitiesError,
		isFetching: isFetchingEntities,
		refetch: refetchEntities,
	} = useIntegrationEntities(integration?.list_entities_data_provider_id);

	// Auto-match hook
	const {
		suggestions: autoMatchSuggestions,
		matchStats,
		isMatching,
		runAutoMatch,
		acceptSuggestion,
		rejectSuggestion,
		acceptAll,
		clearSuggestions,
	} = useAutoMatch({
		organizations: organizations.map(
			(org: { id: string; name: string }) => ({
				id: org.id,
				name: org.name,
			}),
		),
		entities: entities.map((e) => ({ value: e.value, label: e.label })),
		existingMappings: mappings
			.filter((m) => m.organization_id != null)
			.map((m) => ({
				organization_id: m.organization_id!,
				entity_id: m.entity_id,
			})),
	});

	// OAuth config from integration (now returned directly from GET /api/integrations/{id})
	const oauthConfig = integration?.oauth_config;

	// OAuth status helpers using the oauth_config from integration
	const isOAuthConnected = oauthConfig?.status === "completed";
	const isOAuthExpired =
		oauthConfig?.expires_at && isExpired(oauthConfig.expires_at);
	const isOAuthExpiringSoon =
		oauthConfig?.expires_at &&
		!isOAuthExpired &&
		expiresSoon(oauthConfig.expires_at, 15); // 15 minutes matches the refresh scheduler interval
	const canUseAuthCodeFlow =
		!!oauthConfig && oauthConfig.oauth_flow_type !== "client_credentials";

	// Combine organizations with their mappings using useMemo
	const orgsWithMappings = useMemo((): OrgWithMapping[] => {
		if (isLoadingOrgs || isLoadingIntegration) {
			return [];
		}
		return organizations.map((org: { id: string; name: string }) => {
			const existingMapping = mappings.find(
				(m) => m.organization_id === org.id,
			);

			const formData: MappingFormData = existingMapping
				? {
						organization_id:
							existingMapping.organization_id ?? org.id,
						entity_id: existingMapping.entity_id,
						entity_name: existingMapping.entity_name || "",
						oauth_token_id:
							existingMapping.oauth_token_id || undefined,
						config: existingMapping.config || {},
					}
				: {
						organization_id: org.id,
						entity_id: "",
						entity_name: "",
						config: {},
					};

			return {
				id: org.id,
				name: org.name,
				mapping: existingMapping,
				formData,
			};
		});
	}, [organizations, mappings, isLoadingOrgs, isLoadingIntegration]);

	// Listen for OAuth success messages from popup window
	useEffect(() => {
		const handleMessage = (event: MessageEvent) => {
			// Verify origin for security
			if (event.origin !== window.location.origin) {
				return;
			}

			// Check if this is an OAuth success message
			if (event.data?.type === "oauth_success") {
				// Refresh integration (includes OAuth config)
				refetchIntegration();
				// Also refresh per-mapping data (entity_id, oauth_token_id) so
				// the table row updates without a manual page refresh.
				if (integrationId) {
					queryClient.invalidateQueries({
						queryKey: [
							"get",
							"/api/integrations/{integration_id}/mappings",
							{
								params: {
									path: { integration_id: integrationId },
								},
							},
						],
					});
				}
				toast.success("OAuth connection established successfully");
			}
		};

		window.addEventListener("message", handleMessage);

		// Cleanup listener on unmount
		return () => {
			window.removeEventListener("message", handleMessage);
		};
	}, [refetchIntegration, queryClient, integrationId]);

	const [mappingSaveError, setMappingSaveError] = useState<string | null>(
		null,
	);
	const [failedMappingBatch, setFailedMappingBatch] = useState<Array<{
		organization_id: string;
		entity_id: string;
		entity_name?: string;
	}> | null>(null);
	const mappingSaveActive = useRef(false);
	const saveMappings = async (
		mappingsToSave: Array<{
			organization_id: string;
			entity_id: string;
			entity_name?: string;
		}>,
	) => {
		if (mappingSaveActive.current) return;
		mappingSaveActive.current = true;
		setMappingSaveError(null);
		setFailedMappingBatch(null);
		try {
			const result = await batchMutation.mutateAsync({
				params: { path: { integration_id: integrationId! } },
				body: { mappings: mappingsToSave },
			});
			const total = result.created + result.updated;
			if (result.errors?.length) {
				setMappingSaveError(
					`Saved ${total} mapping(s), but ${result.errors.length} failed. Review your mappings and retry the failed items.`,
				);
				setFailedMappingBatch(
					failedMappings(mappingsToSave, result.errors),
				);
			} else {
				toast.success(`Saved ${total} mapping(s)`);
			}
		} catch {
			setMappingSaveError(
				"Unable to save mappings. Your attempted values are available to retry.",
			);
			setFailedMappingBatch(mappingsToSave);
		} finally {
			mappingSaveActive.current = false;
		}
	};

	const handleEntitySelect = async (
		orgId: string,
		entityId: string,
		entityName?: string,
	) => {
		try {
			await saveMappings([
				{
					organization_id: orgId,
					entity_id: entityId,
					entity_name: entityName,
				},
			]);
		} catch {
			toast.error("Failed to save mapping");
		}
	};

	const handleDeleteMappingClick = (org: OrgWithMapping) => {
		setDeleteMappingConfirm(org);
	};

	const handleConnectMapping = async (org: OrgWithMapping) => {
		if (!integrationId) return;
		if (pendingMappingActionRef.current) return;
		const pendingAction: PendingMappingAction = {
			orgId: org.id,
			action: "connect",
		};
		setPendingMappingAction(pendingAction);
		// Use the same redirect_uri shape as the integration-level Connect so the
		// shared OAuthCallback page (route /oauth/callback/:integrationId) handles
		// both flows. The state token carries mapping_id for the per-mapping path.
		const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;

		try {
			// If there's no mapping row yet, create an empty one so the OAuth
			// callback has something to link the token to. entity_id will be
			// auto-populated from the callback when the provider has entity_id_source
			// configured.
			let mappingId = org.mapping?.id;
			if (!mappingId) {
				try {
					const created = await createMappingMutation.mutateAsync({
						params: { path: { integration_id: integrationId } },
						body: {
							organization_id: org.id,
							entity_id: org.formData.entity_id ?? "",
							entity_name: org.formData.entity_name ?? "",
						},
					});
					mappingId = created.id;
				} catch {
					toast.error(
						"Failed to create mapping for OAuth connection",
					);
					return;
				}
			}

			const response = await authorizeMappingMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
				body: { redirect_uri: redirectUri },
			});

			// Match the integration-level Connect: open in a centered popup so the
			// user stays on the integration page and the existing
			// postMessage(oauth_success) listener refreshes state on close.
			const width = 600;
			const height = 700;
			const left = window.screenX + (window.outerWidth - width) / 2;
			const top = window.screenY + (window.outerHeight - height) / 2;
			window.open(
				response.authorization_url,
				"oauth_popup",
				`width=${width},height=${height},left=${left},top=${top},scrollbars=yes`,
			);
		} catch {
			toast.error("Failed to start OAuth connection");
		} finally {
			clearPendingMappingAction(pendingAction);
		}
	};

	const handleDisconnectMapping = async (org: OrgWithMapping) => {
		if (!integrationId) return;
		if (pendingMappingActionRef.current) return;
		const mappingId = org.mapping?.id;
		if (!mappingId) return;
		const pendingAction: PendingMappingAction = {
			orgId: org.id,
			action: "disconnect",
		};
		setPendingMappingAction(pendingAction);
		try {
			await disconnectMappingMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
			});
			toast.success("OAuth connection disconnected");
		} catch {
			toast.error("Failed to disconnect OAuth connection");
		} finally {
			clearPendingMappingAction(pendingAction);
		}
	};

	const handleRefreshMapping = async (org: OrgWithMapping) => {
		if (!integrationId) return;
		if (pendingMappingActionRef.current) return;
		const mappingId = org.mapping?.id;
		if (!mappingId) return;
		const pendingAction: PendingMappingAction = {
			orgId: org.id,
			action: "refresh",
		};
		setPendingMappingAction(pendingAction);
		try {
			await refreshMappingMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: mappingId,
					},
				},
			});
			toast.success("Token refreshed");
		} catch (err) {
			const msg =
				(err as { detail?: string } | undefined)?.detail ??
				"Failed to refresh token";
			toast.error(msg);
		} finally {
			clearPendingMappingAction(pendingAction);
		}
	};

	const handleDeleteMappingConfirm = async () => {
		const org = deleteMappingConfirm;
		if (!integrationId || !org?.mapping)
			throw new Error("Mapping unavailable");
		await deleteMutation.mutateAsync({
			params: {
				path: {
					integration_id: integrationId,
					mapping_id: org.mapping.id,
				},
			},
		});
		toast.success(`Mapping deleted for ${org.name}`);
	};

	// Handle main integration OAuth connect
	const handleIntegrationOAuthConnect = async () => {
		if (!integration?.has_oauth_config || !integrationId) return;

		const redirectUri = `${window.location.origin}/oauth/callback/${integrationId}`;

		authorizeMutation.mutate({
			params: {
				path: { connection_name: integrationId },
				query: { redirect_uri: redirectUri },
			},
		});
	};

	// Handle main integration OAuth refresh
	const handleIntegrationOAuthRefresh = async () => {
		if (!integration?.has_oauth_config || !integrationId) return;

		try {
			await refreshMutation.mutateAsync({
				params: { path: { connection_name: integrationId } },
			});
			refetchIntegration();
		} catch {
			// Error is already handled by the mutation's onError
		}
	};

	// Handle OAuth config deletion
	const handleDeleteOAuthConfig = async () => {
		if (!integrationId) throw new Error("Integration unavailable");
		await deleteOAuthMutation.mutateAsync({
			params: { path: { connection_name: integrationId } },
		});
	};

	const handleOpenConfigDialog = (orgId: string) => {
		const org = orgsWithMappings.find((o) => o.id === orgId);
		if (org) {
			setSelectedOrgForConfig(org);
			setConfigDialogOpen(true);
		}
	};

	const handleSaveOrgConfig = async (config: Record<string, unknown>) => {
		if (!selectedOrgForConfig || !integrationId) return;

		const plan = planOrgConfigSave(selectedOrgForConfig, config);
		if (plan.kind === "noop") return;
		if (plan.kind === "create") {
			// No mapping row yet — create one carrying the overrides so
			// unmapped orgs can still hold per-org config (entity_id may
			// stay empty until the org is mapped to an external entity).
			await createMappingMutation.mutateAsync({
				params: { path: { integration_id: integrationId } },
				body: plan.body,
			});
		} else {
			await updateMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: plan.mapping_id,
					},
				},
				body: plan.body,
			});
		}
		toast.success(`Configuration saved for ${selectedOrgForConfig.name}`);
		// Cache invalidation in useCreateMapping/useUpdateMapping handles refetch
	};

	// Configuration Defaults Dialog handlers
	const handleOpenDefaultsDialog = () => {
		setDefaultsError(null);
		if (!integration?.config_schema) return;
		// Initialize form with current default values from config_defaults
		const currentDefaults: Record<string, unknown> = {};
		integration.config_schema.forEach((field) => {
			currentDefaults[field.key] =
				integration.config_defaults?.[field.key] ?? "";
		});
		setDefaultsFormValues(currentDefaults);
		setDefaultsDialogOpen(true);
	};

	const handleSaveDefaults = async () => {
		if (
			!integrationId ||
			!integration?.config_schema ||
			updateConfigMutation.isPending
		)
			return;
		setDefaultsError(null);

		// Validate form values before save
		const validationErrors: string[] = [];
		for (const field of integration.config_schema) {
			const value = defaultsFormValues[field.key];

			// Skip empty values (they're allowed)
			if (value === "" || value === null || value === undefined) {
				continue;
			}

			// Validate int fields
			if (field.type === "int") {
				const numValue =
					typeof value === "string" ? Number(value) : value;
				if (
					!Number.isSafeInteger(numValue) ||
					(typeof value === "string" &&
						!/^[+-]?\d+$/.test(value.trim()))
				) {
					validationErrors.push(
						`${field.key} must be a valid integer`,
					);
				}
			}

			// Validate JSON fields
			if (field.type === "json") {
				if (typeof value === "string") {
					try {
						JSON.parse(value);
					} catch {
						validationErrors.push(
							`${field.key} must be valid JSON`,
						);
					}
				}
			}
		}

		if (validationErrors.length > 0) {
			setDefaultsError(validationErrors.join(", "));
			return;
		}

		try {
			// Build config object from form values
			const config: Record<string, unknown> = {};
			for (const [key, value] of Object.entries(defaultsFormValues)) {
				// Only include non-empty values
				if (value !== "" && value !== null && value !== undefined) {
					config[key] =
						integration.config_schema.find(
							(field) => field.key === key,
						)?.type === "int"
							? Number(value)
							: value;
				}
			}

			await updateConfigMutation.mutateAsync({
				params: { path: { integration_id: integrationId } },
				body: { config },
			});

			toast.success("Configuration defaults updated");
			setDefaultsDialogOpen(false);
		} catch {
			setDefaultsError(
				"Unable to save configuration defaults. Your values are retained; try saving again.",
			);
		}
	};

	const handleAcceptSuggestion = async (orgId: string) => {
		const suggestion = acceptSuggestion(orgId);
		if (suggestion) {
			try {
				await saveMappings([
					{
						organization_id: orgId,
						entity_id: suggestion.entityId,
						entity_name: suggestion.entityName,
					},
				]);
			} catch {
				toast.error("Failed to save mapping");
			}
		}
	};

	const handleAcceptAllSuggestions = async () => {
		const suggestions = acceptAll();
		if (suggestions.length === 0) return;
		try {
			await saveMappings(
				suggestions.map((s) => ({
					organization_id: s.organizationId,
					entity_id: s.entityId,
					entity_name: s.entityName,
				})),
			);
		} catch {
			toast.error("Failed to save mappings");
		}
	};

	// Handle test connection
	const handleTestConnection = async () => {
		if (!integrationId) return;

		setTestResult(null);

		try {
			const result = await testMutation.mutateAsync({
				params: { path: { integration_id: integrationId } },
				body: { organization_id: testOrgId, endpoint: testEndpoint },
			});
			setTestResult(result);
		} catch {
			setTestResult({
				success: false,
				message:
					"Unable to test the connection. Check the endpoint and try again.",
			});
		}
	};

	const handleOpenTestDialog = () => {
		// Default to Global (null) - tests with integration defaults only
		setTestOrgId(null);
		setTestEndpoint("/");
		setTestResult(null);
		setTestDialogOpen(true);
	};

	const isLoading = isLoadingIntegration || isLoadingOrgs;

	if (isLoading) {
		return (
			<div
				role="status"
				aria-label="Loading integration"
				className="space-y-6"
			>
				<Skeleton className="h-12 w-64 max-w-full" />
				<Skeleton className="h-64 w-full" />
			</div>
		);
	}

	if ((isIntegrationError && !integration) || (isOrgsError && !orgsData)) {
		return (
			<div className="space-y-4">
				{isIntegrationError && !integration && (
					<IntegrationReadError
						resource="integration"
						cached={false}
						pending={isFetchingIntegration}
						onRetry={() => {
							void refetchIntegration();
						}}
					/>
				)}
				{isOrgsError && !orgsData && (
					<IntegrationReadError
						resource="organizations"
						cached={false}
						pending={isFetchingOrgs}
						onRetry={() => {
							void refetchOrgs();
						}}
					/>
				)}
				<Button asChild variant="outline" className="min-h-11">
					<Link to="/integrations">Back to Integrations</Link>
				</Button>
			</div>
		);
	}

	if (!integration) {
		return (
			<div className="flex flex-col items-center justify-center py-12">
				<XCircle className="h-12 w-12 text-destructive" />
				<h3 className="mt-4 text-lg font-semibold">
					Integration not found
				</h3>
				<Button asChild variant="outline" className="mt-4 min-h-11">
					<Link to="/integrations">Back to Integrations</Link>
				</Button>
			</div>
		);
	}

	return (
		<PageWorkspace>
			<IntegrationPageHeader
				name={integration.name}
				description={
					(
						integration as typeof integration & {
							description?: string;
						}
					).description
				}
				onTest={handleOpenTestDialog}
				onGenerateSDK={() => setGenerateSDKDialogOpen(true)}
				onEdit={() => setEditDialogOpen(true)}
			/>
			<PageScrollArea className="space-y-6 lg:flex lg:flex-col lg:gap-6 lg:space-y-0">
				{isIntegrationError && (
					<IntegrationReadError
						resource="integration"
						cached
						pending={isFetchingIntegration}
						onRetry={() => {
							void refetchIntegration();
						}}
					/>
				)}
				{isOrgsError && (
					<IntegrationReadError
						resource="organizations"
						cached
						pending={isFetchingOrgs}
						onRetry={() => {
							void refetchOrgs();
						}}
					/>
				)}
				{/* Config Defaults & OAuth Status */}
				<IntegrationOverview
					integration={integration}
					oauthConfig={oauthConfig}
					isOAuthConnected={isOAuthConnected}
					isOAuthExpired={isOAuthExpired}
					isOAuthExpiringSoon={isOAuthExpiringSoon}
					canUseAuthCodeFlow={canUseAuthCodeFlow}
					onOpenDefaultsDialog={handleOpenDefaultsDialog}
					onOAuthConnect={handleIntegrationOAuthConnect}
					onOAuthRefresh={handleIntegrationOAuthRefresh}
					onEditOAuthConfig={() => setEditingOAuthConfig(true)}
					onDeleteOAuthConfig={() => setDeleteOAuthDialogOpen(true)}
					onCreateOAuthConfig={() => setOAuthConfigDialogOpen(true)}
					isAuthorizePending={authorizeMutation.isPending}
					isRefreshPending={refreshMutation.isPending}
				/>

				{/* Tabs for Mappings and Config Overrides */}
				<Tabs
					defaultValue="mappings"
					className="flex min-h-0 flex-col gap-4"
				>
					<TabsList
						aria-label="Integration views"
						className="grid grid-cols-2 w-full group-data-horizontal/tabs:h-auto sm:w-fit"
					>
						<TabsTrigger
							className="h-auto min-h-11 whitespace-normal"
							value="mappings"
						>
							Mappings
						</TabsTrigger>
						<TabsTrigger
							className="h-auto min-h-11 whitespace-normal"
							value="config-overrides"
						>
							Config Overrides
						</TabsTrigger>
					</TabsList>

					<TabsContent
						value="mappings"
						className="flex min-h-0 flex-col"
					>
						<div className="min-w-0">
							{mappingSaveError && (
								<div
									role="alert"
									className="mb-4 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center rounded-[var(--bf-radius-control)] border border-destructive/20 bg-destructive/10 p-4 text-sm"
								>
									<p className="min-w-0 flex-1">
										{mappingSaveError}
									</p>
									{failedMappingBatch && (
										<Button
											type="button"
											variant="outline"
											className="min-h-11"
											disabled={batchMutation.isPending}
											onClick={() => {
												void saveMappings(
													failedMappingBatch,
												);
											}}
										>
											Retry mapping save
										</Button>
									)}
								</div>
							)}
							<fieldset
								disabled={batchMutation.isPending}
								className="min-w-0"
							>
								{batchMutation.isPending && (
									<p
										role="status"
										className="mb-3 text-sm text-muted-foreground"
									>
										Saving mappings…
									</p>
								)}
								<IntegrationMappingsTab
									orgsWithMappings={orgsWithMappings}
									entities={entities}
									isLoadingEntities={isLoadingEntities}
									isEntitiesError={isEntitiesError}
									isFetchingEntities={isFetchingEntities}
									onRetryEntities={() => {
										void refetchEntities();
									}}
									hasDataProvider={
										!!integration.list_entities_data_provider_id
									}
									hasOAuth={!!integration.has_oauth_config}
									configSchema={
										integration?.config_schema || []
									}
									configDefaults={
										integration?.config_defaults
									}
									autoMatchSuggestions={autoMatchSuggestions}
									matchStats={matchStats}
									isMatching={isMatching}
									isDeletePending={deleteMutation.isPending}
									onRunAutoMatch={runAutoMatch}
									onAcceptAllSuggestions={
										handleAcceptAllSuggestions
									}
									onClearSuggestions={clearSuggestions}
									onAcceptSuggestion={handleAcceptSuggestion}
									onRejectSuggestion={rejectSuggestion}
									onUpdateOrgMapping={handleEntitySelect}
									onOpenConfigDialog={handleOpenConfigDialog}
									onDeleteMapping={handleDeleteMappingClick}
									onConnectMapping={handleConnectMapping}
									onDisconnectMapping={
										handleDisconnectMapping
									}
									onRefreshMapping={handleRefreshMapping}
									pendingMappingAction={pendingMappingAction}
								/>
							</fieldset>
						</div>
					</TabsContent>

					<TabsContent
						value="config-overrides"
						className="flex min-h-0 flex-1 flex-col"
					>
						<Card>
							<CardHeader>
								<CardTitle>Configuration Overrides</CardTitle>
								<CardDescription>
									Manage organization-specific configuration
									overrides
								</CardDescription>
							</CardHeader>
							<CardContent>
								<ConfigOverridesTab
									orgsWithMappings={orgsWithMappings}
									configSchema={
										integration?.config_schema || []
									}
									integrationId={integrationId || ""}
								/>
							</CardContent>
						</Card>
					</TabsContent>
				</Tabs>
			</PageScrollArea>

			{/* OAuth Configuration Dialog (Create) */}
			{integrationId && (
				<CreateOAuthConnectionDialog
					open={oauthConfigDialogOpen}
					onOpenChange={setOAuthConfigDialogOpen}
					integrationId={integrationId}
				/>
			)}

			{/* OAuth Configuration Dialog (Edit) */}
			{integrationId && (
				<CreateOAuthConnectionDialog
					open={editingOAuthConfig}
					onOpenChange={setEditingOAuthConfig}
					integrationId={integrationId}
					editConnectionName={integrationId}
				/>
			)}

			{/* Edit Integration Dialog */}
			<CreateIntegrationDialog
				open={editDialogOpen}
				onOpenChange={setEditDialogOpen}
				editIntegrationId={integrationId}
				initialData={integration}
			/>

			{/* Org Config Dialog */}
			{selectedOrgForConfig && (
				<OrgConfigDialog
					open={configDialogOpen}
					onOpenChange={setConfigDialogOpen}
					orgId={selectedOrgForConfig.id}
					orgName={selectedOrgForConfig.name}
					configSchema={integration?.config_schema || []}
					currentConfig={selectedOrgForConfig.formData.config}
					onSave={handleSaveOrgConfig}
				/>
			)}

			{/* Configuration Defaults Dialog */}
			<IntegrationDefaultsDialog
				error={defaultsError}
				open={defaultsDialogOpen}
				onOpenChange={setDefaultsDialogOpen}
				configSchema={integration?.config_schema || []}
				formValues={defaultsFormValues}
				onFormValuesChange={setDefaultsFormValues}
				onSave={handleSaveDefaults}
				isSaving={updateConfigMutation.isPending}
			/>

			<IntegrationDeleteDialog
				open={deleteMappingConfirm !== null}
				onOpenChange={(open) => {
					if (!open) setDeleteMappingConfirm(null);
				}}
				title="Delete Mapping"
				onConfirm={handleDeleteMappingConfirm}
			>
				Delete the mapping for{" "}
				<strong>{deleteMappingConfirm?.name}</strong>? This removes the
				organization's integration configuration and cannot be undone.
			</IntegrationDeleteDialog>
			<IntegrationDeleteDialog
				open={deleteOAuthDialogOpen}
				onOpenChange={setDeleteOAuthDialogOpen}
				title="Delete OAuth Configuration"
				onConfirm={handleDeleteOAuthConfig}
			>
				Delete the OAuth configuration for{" "}
				<strong>{integration?.name}</strong>? This removes the OAuth
				connection and stored tokens and cannot be undone.
			</IntegrationDeleteDialog>

			{/* Generate SDK Dialog */}
			{integrationId && (
				<GenerateSDKDialog
					open={generateSDKDialogOpen}
					onOpenChange={setGenerateSDKDialogOpen}
					integrationId={integrationId}
					integrationName={integration?.name || ""}
					hasOAuth={integration?.has_oauth_config || false}
				/>
			)}

			{/* Test Connection Dialog */}
			<IntegrationTestPanel
				open={testDialogOpen}
				onOpenChange={setTestDialogOpen}
				testOrgId={testOrgId}
				onTestOrgIdChange={setTestOrgId}
				testEndpoint={testEndpoint}
				onTestEndpointChange={setTestEndpoint}
				testResult={testResult}
				onClearResult={() => setTestResult(null)}
				onTest={handleTestConnection}
				isTestPending={testMutation.isPending}
			/>
		</PageWorkspace>
	);
}
