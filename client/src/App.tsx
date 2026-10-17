import { Suspense, useEffect } from "react";
import {
	Route,
	RouterProvider,
	createBrowserRouter,
	createRoutesFromElements,
	useLocation,
	Outlet,
	matchPath,
} from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { ContentLayout } from "@/components/layout/ContentLayout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PageErrorBoundary } from "@/components/PageErrorBoundary";
import { EditorOverlay } from "@/components/editor/EditorOverlay";
import { UnifiedDock } from "@/components/layout/UnifiedDock";
import { QuickAccess } from "@/components/quick-access/QuickAccess";
import { PageLoader } from "@/components/PageLoader";
import { ApplicationUpdateGate } from "@/components/ApplicationUpdateScreen";
import { RouteTransitionProgress } from "@/components/layout/RouteTransitionProgress";
import { useEditorStore } from "@/stores/editorStore";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { AuthProvider } from "@/contexts/AuthContext";
import { OrgScopeProvider, useOrgScope } from "@/contexts/OrgScopeContext";
import { useApplicationName } from "@/lib/applicationName";
import {
	KeyboardProvider,
	useCmdCtrlShortcut,
} from "@/contexts/KeyboardContext";
import { lazyWithReload } from "@/lib/lazy-with-reload";
import { RunFormRoute } from "@/pages/run-form-route";
import { RouteOpeningState } from "@/components/layout/RouteOpeningState";
import { RouteLoadError } from "@/components/layout/RouteLoadError";
import {
	agentDetailLoader,
	applicationDetailLoader,
} from "@/lib/detail-route-loaders";

// Lazy load all page components for code splitting
const Home = lazyWithReload(() =>
	import("@/pages/Home").then((m) => ({ default: m.Home })),
);
const Dashboard = lazyWithReload(() =>
	import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const Config = lazyWithReload(() =>
	import("@/pages/Config").then((m) => ({ default: m.Config })),
);
const Roles = lazyWithReload(() =>
	import("@/pages/Roles").then((m) => ({ default: m.Roles })),
);
const RoleDetail = lazyWithReload(() =>
	import("@/pages/RoleDetail").then((m) => ({ default: m.RoleDetail })),
);
const Solutions = lazyWithReload(() =>
	import("@/pages/Solutions").then((m) => ({ default: m.Solutions })),
);
const SolutionDetail = lazyWithReload(() =>
	import("@/pages/SolutionDetail").then((m) => ({
		default: m.SolutionDetail,
	})),
);
const Users = lazyWithReload(() =>
	import("@/pages/Users").then((m) => ({ default: m.Users })),
);
const Identities = lazyWithReload(() =>
	import("@/pages/Identities").then((m) => ({ default: m.Identities })),
);
const UserAccessPage = lazyWithReload(() =>
	import("@/pages/UserAccessPage").then((m) => ({
		default: m.UserAccessPage,
	})),
);
const Organizations = lazyWithReload(() =>
	import("@/pages/Organizations").then((m) => ({ default: m.Organizations })),
);
const Forms = lazyWithReload(() =>
	import("@/pages/Forms").then((m) => ({ default: m.Forms })),
);
const FleetPage = lazyWithReload(() =>
	import("@/pages/agents/FleetPage").then((m) => ({ default: m.FleetPage })),
);
const AgentDetailPage = lazyWithReload(() =>
	import("@/pages/agents/AgentDetailPage").then((m) => ({
		default: m.AgentDetailPage,
	})),
);
const AgentReviewPage = lazyWithReload(() =>
	import("@/pages/agents/AgentReviewPage").then((m) => ({
		default: m.AgentReviewPage,
	})),
);
const AgentTuneWorkbench = lazyWithReload(() =>
	import("@/pages/agents/AgentTuneWorkbench").then((m) => ({
		default: m.AgentTuneWorkbench,
	})),
);
const AgentRunDetailPage = lazyWithReload(() =>
	import("@/pages/agents/AgentRunDetailPage").then((m) => ({
		default: m.AgentRunDetailPage,
	})),
);
const FormBuilder = lazyWithReload(() =>
	import("@/pages/FormBuilder").then((m) => ({ default: m.FormBuilder })),
);
const Workflows = lazyWithReload(() =>
	import("@/pages/Workflows").then((m) => ({ default: m.Workflows })),
);
const ServiceDetail = lazyWithReload(() =>
	import("@/pages/ServiceDetail").then((m) => ({
		default: m.ServiceDetail,
	})),
);
const Services = lazyWithReload(() =>
	import("@/pages/Services").then((m) => ({ default: m.Services })),
);
const ExecuteWorkflow = lazyWithReload(() =>
	import("@/pages/ExecuteWorkflow").then((m) => ({
		default: m.ExecuteWorkflow,
	})),
);
const ExecutionHistory = lazyWithReload(() =>
	import("@/pages/ExecutionHistory").then((m) => ({
		default: m.ExecutionHistory,
	})),
);
const ExecutionDetails = lazyWithReload(() =>
	import("@/pages/ExecutionDetails").then((m) => ({
		default: m.ExecutionDetails,
	})),
);
const OAuthCallback = lazyWithReload(() =>
	import("@/pages/OAuthCallback").then((m) => ({ default: m.OAuthCallback })),
);
const Integrations = lazyWithReload(() =>
	import("@/pages/Integrations").then((m) => ({ default: m.Integrations })),
);
const IntegrationDetail = lazyWithReload(() =>
	import("@/pages/IntegrationDetail").then((m) => ({
		default: m.IntegrationDetail,
	})),
);
const Events = lazyWithReload(() =>
	import("@/pages/Events").then((m) => ({ default: m.Events })),
);
const Settings = lazyWithReload(() =>
	import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const UserSettings = lazyWithReload(() =>
	import("@/pages/UserSettings").then((m) => ({ default: m.UserSettings })),
);
const DiagnosticsPage = lazyWithReload(() =>
	import("@/pages/diagnostics/DiagnosticsPage").then((m) => ({
		default: m.DiagnosticsPage,
	})),
);
const AuditLogPage = lazyWithReload(() =>
	import("@/pages/audit/AuditLogPage").then((m) => ({
		default: m.AuditLogPage,
	})),
);
const AccessChecksPage = lazyWithReload(() =>
	import("@/pages/audit/AccessChecksPage").then((m) => ({
		default: m.AccessChecksPage,
	})),
);
const Login = lazyWithReload(() =>
	import("@/pages/Login").then((m) => ({ default: m.Login })),
);
const Setup = lazyWithReload(() =>
	import("@/pages/Setup").then((m) => ({ default: m.Setup })),
);
const MFASetup = lazyWithReload(() =>
	import("@/pages/MFASetup").then((m) => ({ default: m.MFASetup })),
);
const Register = lazyWithReload(() =>
	import("@/pages/Register").then((m) => ({ default: m.Register })),
);
const AuthCallback = lazyWithReload(() =>
	import("@/pages/AuthCallback").then((m) => ({ default: m.AuthCallback })),
);
const MCPCallback = lazyWithReload(() =>
	import("@/pages/MCPCallback").then((m) => ({ default: m.MCPCallback })),
);
const Chat = lazyWithReload(() =>
	import("@/pages/Chat").then((m) => ({ default: m.Chat })),
);
const ChatArtifacts = lazyWithReload(() =>
	import("@/pages/ChatArtifacts").then((m) => ({ default: m.ChatArtifacts })),
);
const ROIReports = lazyWithReload(() =>
	import("@/pages/ROIReports").then((m) => ({
		default: m.ROIReports,
	})),
);
const UsageReports = lazyWithReload(() =>
	import("@/pages/UsageReports").then((m) => ({
		default: m.UsageReports,
	})),
);
const DevicePage = lazyWithReload(() =>
	import("@/pages/DevicePage").then((m) => ({ default: m.DevicePage })),
);
const Tables = lazyWithReload(() =>
	import("@/pages/Tables").then((m) => ({ default: m.Tables })),
);
const Files = lazyWithReload(() =>
	import("@/pages/Files").then((m) => ({ default: m.Files })),
);
const Knowledge = lazyWithReload(() =>
	import("@/pages/Knowledge").then((m) => ({ default: m.Knowledge })),
);
const TableDetail = lazyWithReload(() =>
	import("@/pages/TableDetail").then((m) => ({ default: m.TableDetail })),
);
const Applications = lazyWithReload(() =>
	import("@/pages/Applications").then((m) => ({ default: m.Applications })),
);
const AppCodeEditorPage = lazyWithReload(() =>
	import("@/pages/AppCodeEditorPage").then((m) => ({
		default: m.AppCodeEditorPage,
	})),
);
const ApplicationRunner = lazyWithReload(() =>
	import("@/pages/AppRouter").then((m) => ({
		default: m.AppPublished,
	})),
);
const ApplicationPreview = lazyWithReload(() =>
	import("@/pages/AppRouter").then((m) => ({
		default: m.AppPreview,
	})),
);
const EntityManagement = lazyWithReload(() =>
	import("@/pages/EntityManagement").then((m) => ({
		default: m.EntityManagement,
	})),
);
const MCPServers = lazyWithReload(() =>
	import("@/pages/MCPServers").then((m) => ({ default: m.MCPServers })),
);
const MCPServerDetail = lazyWithReload(() =>
	import("@/pages/MCPServerDetail").then((m) => ({
		default: m.MCPServerDetail,
	})),
);
const MCPConnectionEdit = lazyWithReload(() =>
	import("@/pages/MCPConnectionEdit").then((m) => ({
		default: m.MCPConnectionEdit,
	})),
);
const ProductUpdates = lazyWithReload(() =>
	import("@/pages/ProductUpdates").then((m) => ({
		default: m.ProductUpdates,
	})),
);

export function AppFrame() {
	const { brandingLoaded } = useOrgScope();
	const applicationName = useApplicationName();
	const location = useLocation();
	const isQuickAccessOpen = useQuickAccessStore((state) => state.isOpen);
	const openQuickAccess = useQuickAccessStore(
		(state) => state.openQuickAccess,
	);
	const closeQuickAccess = useQuickAccessStore(
		(state) => state.closeQuickAccess,
	);
	const openEditor = useEditorStore((state) => state.openEditor);
	const isEditorOpen = useEditorStore((state) => state.isOpen);

	// Register Cmd+K shortcut for quick access
	useCmdCtrlShortcut("quick-access", "k", () => {
		openQuickAccess();
	});

	// Register Cmd+/ to toggle code editor
	useCmdCtrlShortcut("toggle-editor", "/", () => {
		if (!isEditorOpen) {
			openEditor();
		}
	});

	// Base browser-tab title. index.html ships the default literal for first
	// paint; once branding resolves, reflect the (possibly custom) product name.
	// The app-runner and app-preview routes drive their own
	// "<App> | <product>" title via useDocumentChrome (AppRouter), so skip those
	// paths here to avoid clobbering them. The app *editor* route
	// (apps/:id/edit/*) does NOT set its own title, so it must NOT be skipped.
	const isAppRunnerRoute =
		(matchPath("/apps/:applicationId/preview/*", location.pathname) !==
			null ||
			matchPath("/apps/:applicationId/*", location.pathname) !== null) &&
		matchPath("/apps/:applicationId/edit/*", location.pathname) === null;
	useEffect(() => {
		if (isAppRunnerRoute) return;
		document.title = applicationName;
	}, [applicationName, isAppRunnerRoute]);

	// Wait for branding colors to load before rendering
	// Logo component handles its own skeleton loading state
	if (!brandingLoaded) {
		return <PageLoader message="Loading application..." fullScreen />;
	}

	return (
		<>
			{/* Quick Access - Cmd+K search */}
			<QuickAccess
				isOpen={isQuickAccessOpen}
				onClose={closeQuickAccess}
			/>

			{/* Editor Overlay - Rendered globally on top of all pages */}
			<PageErrorBoundary>
				<EditorOverlay />
			</PageErrorBoundary>

			{/* Unified Dock - Shows minimized editor */}
			<UnifiedDock />

			<Suspense fallback={<PageLoader />}>
				<Outlet />
			</Suspense>
		</>
	);
}

const routeElements = (
	<>
		{/* Public routes - no auth required */}
		<Route path="login" element={<Login />} />
		<Route path="setup" element={<Setup />} />
		<Route path="accept-invite" element={<Register />} />
		<Route path="mfa-setup" element={<MFASetup />} />
		<Route path="auth/callback/:provider" element={<AuthCallback />} />
		<Route path="mcp/callback" element={<MCPCallback />} />

		{/* Device authorization - requires auth, handles redirect internally */}
		<Route path="device" element={<DevicePage />} />

		{/* OAuth Callback - Public (no auth, no layout) */}
		<Route
			path="oauth/callback/:integrationId"
			element={<OAuthCallback />}
		/>

		{/* Application Preview - Full screen for developers */}
		<Route
			path="apps/:applicationId/preview/*"
			loader={applicationDetailLoader(true)}
			errorElement={<RouteLoadError />}
			element={
				<ProtectedRoute
					requirePermission={{
						permission: "apps.read",
					}}
				>
					<ApplicationPreview />
				</ProtectedRoute>
			}
		/>

		{/* Application Runner - Published apps (full screen) */}
		<Route
			path="apps/:applicationId/*"
			loader={applicationDetailLoader(false)}
			errorElement={<RouteLoadError />}
			element={
				<ProtectedRoute
					requirePermission={{
						permission: "apps.readbasic",
					}}
				>
					<ApplicationRunner />
				</ProtectedRoute>
			}
		/>
		<Route
			path="embedded/forms/public/:publicKey"
			element={
				<ProtectedRoute requireOrgUser>
					<RunFormRoute />
				</ProtectedRoute>
			}
		/>
		<Route
			path="embedded/forms/hmac/:formId"
			element={
				<ProtectedRoute requireOrgUser>
					<RunFormRoute />
				</ProtectedRoute>
			}
		/>

		<Route path="/" element={<Layout />}>
			{/* Home is available to authenticated users. */}
			<Route index element={<Home />} />
			<Route
				path="whats-new"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "settings.read",
							at: "global",
						}}
					>
						<ProductUpdates />
					</ProtectedRoute>
				}
			/>
			<Route
				path="dashboard"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "metrics.read",
						}}
					>
						<Dashboard />
					</ProtectedRoute>
				}
			/>

			{/* Workflows */}
			<Route
				path="workflows"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "workflows.read",
						}}
					>
						<Workflows />
					</ProtectedRoute>
				}
			/>
			<Route
				path="services"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "platform.read",
						}}
					>
						<Services />
					</ProtectedRoute>
				}
			/>
			<Route
				path="services/:serviceId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "platform.read",
						}}
					>
						<ServiceDetail />
					</ProtectedRoute>
				}
			/>
			<Route
				path="workflows/:workflowName/execute"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "workflows.execute",
						}}
					>
						<ExecuteWorkflow />
					</ProtectedRoute>
				}
			/>

			{/* Forms */}
			<Route
				path="forms"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "forms.readbasic",
						}}
					>
						<Forms />
					</ProtectedRoute>
				}
			/>
			<Route
				path="execute/:formId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "forms.readbasic",
						}}
					>
						<RunFormRoute />
					</ProtectedRoute>
				}
			/>

			{/* Form Builder */}
			<Route
				path="forms/new"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "forms.readwrite",
						}}
					>
						<FormBuilder />
					</ProtectedRoute>
				}
			/>
			<Route
				path="forms/:formId/edit"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "forms.readwrite",
						}}
					>
						<FormBuilder />
					</ProtectedRoute>
				}
			/>

			{/* History */}
			<Route
				path="history"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "executions.readbasic",
						}}
					>
						<ExecutionHistory />
					</ProtectedRoute>
				}
			/>

			{/* Organizations - organizations.read anywhere */}
			<Route
				path="organizations"
				element={
					<ProtectedRoute
						requirePermission={{ permission: "organizations.read" }}
					>
						<Organizations />
					</ProtectedRoute>
				}
			/>

			{/* Users - users.read anywhere */}
			<Route
				path="users"
				element={
					<ProtectedRoute
						requirePermission={{ permission: "users.read" }}
					>
						<Users />
					</ProtectedRoute>
				}
			/>
			<Route
				path="users/identities"
				element={
					<ProtectedRoute
						requirePermission={{ permission: "users.read" }}
					>
						<Identities />
					</ProtectedRoute>
				}
			/>
			<Route
				path="users/:userId"
				element={
					<ProtectedRoute
						requirePermission={{ permission: "users.read" }}
					>
						<UserAccessPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="users/:userId/:tab"
				element={
					<ProtectedRoute
						requirePermission={{ permission: "users.read" }}
					>
						<UserAccessPage />
					</ProtectedRoute>
				}
			/>

			{/* Roles - roles.read platform-wide */}
			<Route
				path="roles"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "roles.read",
							at: "global",
						}}
					>
						<Roles />
					</ProtectedRoute>
				}
			/>
			<Route
				path="roles/:roleId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "roles.read",
							at: "global",
						}}
					>
						<RoleDetail />
					</ProtectedRoute>
				}
			/>
			<Route
				path="roles/:roleId/:tab"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "roles.read",
							at: "global",
						}}
					>
						<RoleDetail />
					</ProtectedRoute>
				}
			/>

			{/* Solutions */}
			<Route
				path="solutions"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "solutions.read",
						}}
					>
						<Solutions />
					</ProtectedRoute>
				}
			/>
			<Route
				path="solutions/:solutionId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "solutions.read",
						}}
					>
						<SolutionDetail />
					</ProtectedRoute>
				}
			/>

			{/* Config */}
			<Route
				path="config"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "configs.read",
						}}
					>
						<Config />
					</ProtectedRoute>
				}
			/>

			{/* Data Tables */}
			<Route
				path="tables"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "tables.read",
						}}
					>
						<Tables />
					</ProtectedRoute>
				}
			/>
			<Route
				path="tables/:tableId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "tables.read",
						}}
					>
						<TableDetail />
					</ProtectedRoute>
				}
			/>

			{/* Files */}
			<Route
				path="files"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "filepolicies.read",
						}}
					>
						<Files />
					</ProtectedRoute>
				}
			/>

			{/* Applications */}
			<Route
				path="apps"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "apps.readbasic",
						}}
					>
						<Applications />
					</ProtectedRoute>
				}
			/>
			<Route
				path="apps/new"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "apps.readwrite",
						}}
					>
						<AppCodeEditorPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="apps/:applicationId/edit/*"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "apps.readwrite",
						}}
					>
						<AppCodeEditorPage />
					</ProtectedRoute>
				}
			/>

			{/* Agents */}
			<Route
				path="agents"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "agents.readbasic",
						}}
					>
						<FleetPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="agents/new"
				element={
					<ProtectedRoute>
						<AgentDetailPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="agents/:id"
				loader={agentDetailLoader}
				errorElement={<RouteLoadError />}
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "agents.readbasic",
						}}
					>
						<AgentDetailPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="agents/:id/review"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "agents.readbasic",
						}}
					>
						<AgentReviewPage />
					</ProtectedRoute>
				}
			/>
			<Route
				path="agents/:id/tune"
				element={
					<ProtectedRoute>
						<AgentTuneWorkbench />
					</ProtectedRoute>
				}
			/>
			<Route
				path="agents/:agentId/runs/:runId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "agents.readbasic",
						}}
					>
						<AgentRunDetailPage />
					</ProtectedRoute>
				}
			/>
			{/* Knowledge */}
			<Route
				path="knowledge"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "knowledge.read",
						}}
					>
						<Knowledge />
					</ProtectedRoute>
				}
			/>

			{/* Entity Management */}
			<Route
				path="entity-management"
				element={
					<ProtectedRoute
						requirePermission={{
							allOf: [
								{ permission: "workflows.read" },
								{ permission: "forms.readbasic" },
								{ permission: "agents.readbasic" },
								{ permission: "apps.readbasic" },
								{ permission: "organizations.read" },
								{ permission: "roles.read", at: "global" },
							],
						}}
					>
						<EntityManagement />
					</ProtectedRoute>
				}
			/>

			{/* Integrations */}
			<Route
				path="integrations"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "integrations.read",
						}}
					>
						<Integrations />
					</ProtectedRoute>
				}
			/>
			<Route
				path="integrations/:id"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "integrations.read",
						}}
					>
						<IntegrationDetail />
					</ProtectedRoute>
				}
			/>

			{/* MCP Servers */}
			<Route
				path="mcp-servers"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "mcp.readbasic",
						}}
					>
						<MCPServers />
					</ProtectedRoute>
				}
			/>
			<Route
				path="mcp-servers/:id"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "mcp.readbasic",
						}}
					>
						<MCPServerDetail />
					</ProtectedRoute>
				}
			/>
			<Route
				path="mcp-servers/:serverId/connections/:connectionId/edit"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "mcp.readwrite",
						}}
					>
						<MCPConnectionEdit />
					</ProtectedRoute>
				}
			/>

			{/* Event Sources */}
			<Route
				path="event-sources"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "events.read",
						}}
					>
						<Events />
					</ProtectedRoute>
				}
			/>
			<Route
				path="event-sources/:sourceId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "events.read",
						}}
					>
						<Events />
					</ProtectedRoute>
				}
			/>
			<Route
				path="event-sources/:sourceId/events/:eventId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "events.read",
						}}
					>
						<Events />
					</ProtectedRoute>
				}
			/>

			{/* Settings */}
			<Route
				path="settings"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "settings.read",
							at: "global",
						}}
					>
						<Settings />
					</ProtectedRoute>
				}
			/>
			<Route
				path="settings/:tab"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "settings.read",
							at: "global",
						}}
					>
						<Settings />
					</ProtectedRoute>
				}
			/>

			{/* Diagnostics */}
			<Route
				path="diagnostics"
				element={
					<ProtectedRoute
						requirePermission={{
							allOf: [
								{ permission: "metrics.read", at: "global" },
								{ permission: "platform.read", at: "global" },
							],
						}}
					>
						<DiagnosticsPage />
					</ProtectedRoute>
				}
			/>

			{/* Audit Log */}
			<Route
				path="audit"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "roleassignments.read",
						}}
					>
						<AuditLogPage />
					</ProtectedRoute>
				}
			/>

			{/* Access Checks - roleassignments.read anywhere; the API filters to reach */}
			<Route
				path="audit/access-checks"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "roleassignments.read",
						}}
					>
						<AccessChecksPage />
					</ProtectedRoute>
				}
			/>

			{/* Reports */}
			<Route
				path="reports/roi"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "reports.read",
						}}
					>
						<ROIReports />
					</ProtectedRoute>
				}
			/>
			<Route
				path="reports/usage"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "metrics.read",
							at: "global",
						}}
					>
						<UsageReports />
					</ProtectedRoute>
				}
			/>

			{/* User Settings */}
			<Route
				path="user-settings"
				element={
					<ProtectedRoute>
						<UserSettings />
					</ProtectedRoute>
				}
			/>
			<Route
				path="user-settings/:tab"
				element={
					<ProtectedRoute>
						<UserSettings />
					</ProtectedRoute>
				}
			/>
		</Route>

		{/* ContentLayout - Pages without default padding */}
		<Route path="/" element={<ContentLayout />}>
			{/* Chat */}
			<Route
				path="chat/:conversationId?"
				element={
					<ProtectedRoute>
						<Chat />
					</ProtectedRoute>
				}
			/>
			<Route
				path="chat/artifacts"
				element={
					<ProtectedRoute>
						<ChatArtifacts />
					</ProtectedRoute>
				}
			/>
			{/* Execution Details */}
			<Route
				path="history/:executionId"
				element={
					<ProtectedRoute
						requirePermission={{
							permission: "executions.readbasic",
						}}
						allowHmacFormExecutionResult
					>
						<ExecutionDetails />
					</ProtectedRoute>
				}
			/>
		</Route>
	</>
);

function AppProviders() {
	return (
		<AuthProvider>
			<OrgScopeProvider>
				<KeyboardProvider>
					<RouteTransitionProgress />
					<AppFrame />
				</KeyboardProvider>
			</OrgScopeProvider>
		</AuthProvider>
	);
}

const router = createBrowserRouter(
	createRoutesFromElements(
		<Route
			element={<AppProviders />}
			hydrateFallbackElement={<RouteOpeningState />}
		>
			{routeElements}
		</Route>,
	),
);

function App() {
	return (
		<ApplicationUpdateGate>
			<ErrorBoundary>
				<RouterProvider router={router} />
			</ErrorBoundary>
		</ApplicationUpdateGate>
	);
}

export default App;
