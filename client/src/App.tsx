import { Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { ContentLayout } from "@/components/layout/ContentLayout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PageErrorBoundary } from "@/components/PageErrorBoundary";
import { EditorOverlay } from "@/components/editor/EditorOverlay";
import { UnifiedDock } from "@/components/layout/UnifiedDock";
import { QuickAccess } from "@/components/quick-access/QuickAccess";
import { PageLoader } from "@/components/PageLoader";
import { RouteTransitionProgress } from "@/components/layout/RouteTransitionProgress";
import { useEditorStore } from "@/stores/editorStore";
import { useQuickAccessStore } from "@/stores/quickAccessStore";
import { AuthProvider } from "@/contexts/AuthContext";
import { OrgScopeProvider, useOrgScope } from "@/contexts/OrgScopeContext";
import {
	KeyboardProvider,
	useCmdCtrlShortcut,
} from "@/contexts/KeyboardContext";
import { lazyWithReload } from "@/lib/lazy-with-reload";

// Lazy load all page components for code splitting
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
const Users = lazyWithReload(() =>
	import("@/pages/Users").then((m) => ({ default: m.Users })),
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
const RunForm = lazyWithReload(() =>
	import("@/pages/RunForm").then((m) => ({ default: m.RunForm })),
);
const Workflows = lazyWithReload(() =>
	import("@/pages/Workflows").then((m) => ({ default: m.Workflows })),
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

function AppRoutes() {
	const { brandingLoaded } = useOrgScope();
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
				<Routes>
					{/* Public routes - no auth required */}
					<Route path="login" element={<Login />} />
					<Route path="setup" element={<Setup />} />
					<Route path="accept-invite" element={<Register />} />
					<Route path="mfa-setup" element={<MFASetup />} />
					<Route
						path="auth/callback/:provider"
						element={<AuthCallback />}
					/>
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
						element={
							<ProtectedRoute requirePlatformAdmin>
								<ApplicationPreview />
							</ProtectedRoute>
						}
					/>

					{/* Application Runner - Published apps (full screen) */}
					<Route
						path="apps/:applicationId/*"
						element={
							<ProtectedRoute requireOrgUser>
								<ApplicationRunner />
							</ProtectedRoute>
						}
					/>

					<Route path="/" element={<Layout />}>
						{/* Dashboard - PlatformAdmin only (OrgUsers redirected to /forms) */}
						<Route index element={<Dashboard />} />

						{/* Workflows - PlatformAdmin only */}
						<Route
							path="workflows"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Workflows />
								</ProtectedRoute>
							}
						/>
						<Route
							path="workflows/:workflowName/execute"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<ExecuteWorkflow />
								</ProtectedRoute>
							}
						/>

						{/* Forms - PlatformAdmin or OrgUser */}
						<Route
							path="forms"
							element={
								<ProtectedRoute requireOrgUser>
									<Forms />
								</ProtectedRoute>
							}
						/>
						<Route
							path="execute/:formId"
							element={
								<ProtectedRoute requireOrgUser>
									<RunForm />
								</ProtectedRoute>
							}
						/>

						{/* Form Builder - PlatformAdmin only */}
						<Route
							path="forms/new"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<FormBuilder />
								</ProtectedRoute>
							}
						/>
						<Route
							path="forms/:formId/edit"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<FormBuilder />
								</ProtectedRoute>
							}
						/>

						{/* History - PlatformAdmin or OrgUser */}
						<Route
							path="history"
							element={
								<ProtectedRoute requireOrgUser>
									<ExecutionHistory />
								</ProtectedRoute>
							}
						/>

						{/* Organizations - PlatformAdmin only */}
						<Route
							path="organizations"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Organizations />
								</ProtectedRoute>
							}
						/>

						{/* Users - PlatformAdmin only */}
						<Route
							path="users"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Users />
								</ProtectedRoute>
							}
						/>

						{/* Roles - PlatformAdmin only */}
						<Route
							path="roles"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Roles />
								</ProtectedRoute>
							}
						/>
						<Route
							path="roles/:roleId"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<RoleDetail />
								</ProtectedRoute>
							}
						/>
						<Route
							path="roles/:roleId/:tab"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<RoleDetail />
								</ProtectedRoute>
							}
						/>

						{/* Config - PlatformAdmin only */}
						<Route
							path="config"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Config />
								</ProtectedRoute>
							}
						/>

						{/* Data Tables - PlatformAdmin only */}
						<Route
							path="tables"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Tables />
								</ProtectedRoute>
							}
						/>
						<Route
							path="tables/:tableId"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<TableDetail />
								</ProtectedRoute>
							}
						/>

						{/* Applications List - OrgUser access (with sidebar) */}
						<Route
							path="apps"
							element={
								<ProtectedRoute requireOrgUser>
									<Applications />
								</ProtectedRoute>
							}
						/>
						<Route
							path="apps/new"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<AppCodeEditorPage />
								</ProtectedRoute>
							}
						/>
						<Route
							path="apps/:applicationId/edit/*"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<AppCodeEditorPage />
								</ProtectedRoute>
							}
						/>

						{/* Agents - All authenticated users */}
						<Route
							path="agents"
							element={
								<ProtectedRoute>
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
							element={
								<ProtectedRoute>
									<AgentDetailPage />
								</ProtectedRoute>
							}
						/>
						<Route
							path="agents/:id/review"
							element={
								<ProtectedRoute>
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
								<ProtectedRoute>
									<AgentRunDetailPage />
								</ProtectedRoute>
							}
						/>
						{/* Knowledge - PlatformAdmin only */}
						<Route
							path="knowledge"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Knowledge />
								</ProtectedRoute>
							}
						/>

						{/* Entity Management - PlatformAdmin only */}
						<Route
							path="entity-management"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<EntityManagement />
								</ProtectedRoute>
							}
						/>

						{/* Integrations - PlatformAdmin only */}
						<Route
							path="integrations"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Integrations />
								</ProtectedRoute>
							}
						/>
						<Route
							path="integrations/:id"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<IntegrationDetail />
								</ProtectedRoute>
							}
						/>

						{/* MCP Servers - PlatformAdmin only */}
						<Route
							path="mcp-servers"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<MCPServers />
								</ProtectedRoute>
							}
						/>
						<Route
							path="mcp-servers/:id"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<MCPServerDetail />
								</ProtectedRoute>
							}
						/>
						<Route
							path="mcp-servers/:serverId/connections/:connectionId/edit"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<MCPConnectionEdit />
								</ProtectedRoute>
							}
						/>


{/* Event Sources - PlatformAdmin only */}
						<Route
							path="event-sources"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Events />
								</ProtectedRoute>
							}
						/>
						<Route
							path="event-sources/:sourceId"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Events />
								</ProtectedRoute>
							}
						/>
						<Route
							path="event-sources/:sourceId/events/:eventId"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Events />
								</ProtectedRoute>
							}
						/>

						{/* Settings - PlatformAdmin only */}
						<Route
							path="settings"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Settings />
								</ProtectedRoute>
							}
						/>
						<Route
							path="settings/:tab"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<Settings />
								</ProtectedRoute>
							}
						/>

						{/* Diagnostics - PlatformAdmin only */}
						<Route
							path="diagnostics"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<DiagnosticsPage />
								</ProtectedRoute>
							}
						/>

						{/* Audit Log - PlatformAdmin only */}
						<Route
							path="audit"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<AuditLogPage />
								</ProtectedRoute>
							}
						/>

						{/* Reports - PlatformAdmin only */}
						<Route
							path="reports/roi"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<ROIReports />
								</ProtectedRoute>
							}
						/>
						<Route
							path="reports/usage"
							element={
								<ProtectedRoute requirePlatformAdmin>
									<UsageReports />
								</ProtectedRoute>
							}
						/>

						{/* User Settings - All authenticated users */}
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
						{/* Chat - All authenticated users */}
						<Route
							path="chat"
							element={
								<ProtectedRoute>
									<Chat />
								</ProtectedRoute>
							}
						/>
						<Route
							path="chat/:conversationId"
							element={
								<ProtectedRoute>
									<Chat />
								</ProtectedRoute>
							}
						/>
						{/* Execution Details - PlatformAdmin or OrgUser */}
						<Route
							path="history/:executionId"
							element={
								<ProtectedRoute requireOrgUser>
									<ExecutionDetails />
								</ProtectedRoute>
							}
						/>
					</Route>
				</Routes>
			</Suspense>
		</>
	);
}

function App() {
	return (
		<ErrorBoundary>
			<BrowserRouter>
				<RouteTransitionProgress />
				<AuthProvider>
					<OrgScopeProvider>
						<KeyboardProvider>
							<AppRoutes />
						</KeyboardProvider>
					</OrgScopeProvider>
				</AuthProvider>
			</BrowserRouter>
		</ErrorBoundary>
	);
}

export default App;
