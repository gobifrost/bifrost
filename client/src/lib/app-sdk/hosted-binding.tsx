/**
 * The installable `bifrost` package entry for migrated Apps.
 *
 * It contains only binding code and public types. The existing SDK module graph
 * is created by an immutable server-hosted factory with this App's React peers.
 */
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";

import {
	FileAccessDeniedError,
	FileNotFoundError,
	FilePolicyError,
	TableAccessDeniedError,
	TableNotFoundError,
} from "./errors";
import type {
	BifrostContextValue,
	BifrostHeaderProps,
	BifrostProviderProps,
	RequireRoleProps,
	UseBrandingOptions,
	UseBrandingResult,
	UseFilesOptions,
	UseFilesResult,
	UseOrganizationsOptions,
	UseOrganizationsResult,
	UseTableQuery,
	UseWorkflowMutationState,
	UseWorkflowQueryState,
	UseWorkflowState,
} from "./index.v2";
import type {
	UseInfiniteTableQuery,
	UseInfiniteTableResult,
} from "./use-infinite-table";
import type { UseTableResult } from "./use-table";
import type { HostedSdk, HostedSdkFactory } from "./hosted-contract";
import {
	getPreloadedHostedSdkFactory,
	hostedSdkOrigin,
	loadHostedSdkFactory,
} from "./hosted-loader";

export {
	FileAccessDeniedError,
	FileNotFoundError,
	FilePolicyError,
	TableAccessDeniedError,
	TableNotFoundError,
} from "./errors";
export type {
	BifrostContextValue,
	BifrostHeaderNav,
	BifrostHeaderProps,
	BifrostNavItem,
	BifrostNavLinkProps,
	BifrostProviderProps,
	BifrostUser,
	BrandPalette,
	BrandPaletteTheme,
	BrandingSettings,
	DocumentFilter,
	FileListMetadataItem,
	FileListOptions,
	FileListResult,
	FileMode,
	FileOptions,
	FilterValue,
	Organization,
	RequireRoleProps,
	SignedUrlMethod,
	SignedUrlOptions,
	SignedUrlResult,
	TableChangeEvent,
	TableRow,
	UseBrandingOptions,
	UseBrandingResult,
	UseFilesOptions,
	UseFilesResult,
	UseOrganizationsOptions,
	UseOrganizationsResult,
	UseTableQuery,
	UseWorkflowMutationState,
	UseWorkflowQueryState,
	UseWorkflowState,
} from "./index.v2";

const BindingSdkContext = React.createContext<HostedSdk | null>(null);
let providerSdk: { origin: string; sdk: HostedSdk } | null = null;
let defaultSdkInstance: { origin: string; sdk: HostedSdk } | null = null;
let defaultSdkLoad: { origin: string; promise: Promise<HostedSdk> } | null = null;

const peers = {
	react: React,
	jsxRuntime,
	errors: {
		TableAccessDeniedError,
		TableNotFoundError,
		FileAccessDeniedError,
		FileNotFoundError,
		FilePolicyError,
	},
};

function createSdk(factory: HostedSdkFactory): HostedSdk {
	return factory.createSdk(peers);
}

function factoryFor(baseUrl: string): Promise<HostedSdkFactory> {
	return Promise.resolve(
		getPreloadedHostedSdkFactory(baseUrl) ?? loadHostedSdkFactory(baseUrl),
	);
}

function defaultSdk(): Promise<HostedSdk> {
	const origin = hostedSdkOrigin(window.location.origin);
	if (providerSdk) return Promise.resolve(providerSdk.sdk);
	if (defaultSdkInstance?.origin === origin) return Promise.resolve(defaultSdkInstance.sdk);
	if (defaultSdkLoad?.origin === origin) return defaultSdkLoad.promise;
	const load = factoryFor(window.location.origin).then((factory) => {
		if (providerSdk?.origin === origin) return providerSdk.sdk;
		const sdk = createSdk(factory);
		defaultSdkInstance = { origin, sdk };
		return sdk;
	});
	defaultSdkLoad = { origin, promise: load };
	void load.catch(() => {
		if (defaultSdkLoad?.promise === load) defaultSdkLoad = null;
	});
	return load;
}

function useHostedSdk(): HostedSdk {
	const sdk = React.useContext(BindingSdkContext);
	if (!sdk) {
		throw new Error("Bifrost SDK is not ready. Wrap this component in <BifrostProvider>.");
	}
	return sdk;
}

/**
 * The provider is the one asynchronous binding surface needed by direct Vite
 * development. Its children do not render until their hosted implementation is
 * ready, so downstream hooks still delegate synchronously through App React.
 */
function HostedBifrostProvider({
	props,
	origin,
}: {
	props: BifrostProviderProps;
	origin: string;
}) {
	const [sdk, setSdk] = React.useState<HostedSdk | null>(() => {
		const preloaded = getPreloadedHostedSdkFactory(props.baseUrl);
		if (providerSdk?.origin === origin) return providerSdk.sdk;
		if (defaultSdkInstance?.origin === origin) {
			providerSdk = defaultSdkInstance;
			return defaultSdkInstance.sdk;
		}
		if (defaultSdkLoad?.origin === origin) return null;
		if (!preloaded) return null;
		const next = createSdk(preloaded);
		providerSdk = { origin, sdk: next };
		return next;
	});
	const [error, setError] = React.useState<Error | null>(null);

	React.useEffect(() => {
		let cancelled = false;
		const existingSdk = providerSdk?.origin === origin ? providerSdk.sdk : null;
		const reusableDefaultLoad =
			defaultSdkLoad?.origin === origin ? defaultSdkLoad.promise : null;
		void (existingSdk
			? Promise.resolve(existingSdk)
			: reusableDefaultLoad ?? factoryFor(props.baseUrl).then(createSdk))
			.then((loadedSdk) => {
				if (cancelled) return;
				const next = providerSdk?.origin === origin
					? providerSdk.sdk
					: loadedSdk;
				providerSdk = { origin, sdk: next };
				setSdk(next);
			})
			.catch((cause: unknown) => {
				if (!cancelled) {
					console.error("Unable to load hosted Bifrost SDK", cause);
					setError(cause instanceof Error ? cause : new Error("Unable to load the Bifrost SDK."));
				}
			});
		return () => {
			cancelled = true;
		};
	}, [props.baseUrl, origin]);

	if (error) {
		return <div role="alert">Unable to load this App. Reload and try again.</div>;
	}
	if (!sdk) return <div role="status">Loading App...</div>;
	return (
		<BindingSdkContext.Provider value={sdk}>
			<sdk.BifrostProvider {...props}>{props.children}</sdk.BifrostProvider>
		</BindingSdkContext.Provider>
	);
}

export function BifrostProvider(props: BifrostProviderProps) {
	const origin = hostedSdkOrigin(props.baseUrl);
	return <HostedBifrostProvider key={origin} props={props} origin={origin} />;
}

export function useBifrostContext(): BifrostContextValue {
	return useHostedSdk().useBifrostContext();
}

export function BifrostHeader(props: BifrostHeaderProps) {
	return React.createElement(useHostedSdk().BifrostHeader, props);
}

export function RequireRole(props: RequireRoleProps) {
	return React.createElement(useHostedSdk().RequireRole, props);
}

export function useWorkflow<T = unknown>(workflowRef: string): UseWorkflowState<T> {
	return useHostedSdk().useWorkflow<T>(workflowRef);
}

export function useWorkflowQuery<T = unknown>(
	workflowRef: string,
	params?: Record<string, unknown>,
): UseWorkflowQueryState<T> {
	return useHostedSdk().useWorkflowQuery<T>(workflowRef, params);
}

export function useWorkflowMutation<T = unknown>(workflowRef: string): UseWorkflowMutationState<T> {
	return useHostedSdk().useWorkflowMutation<T>(workflowRef);
}

export function useTable(name: string, query: UseTableQuery = {}): UseTableResult {
	return useHostedSdk().useTable(name, query);
}

export function useInfiniteTable(
	name: string,
	query: UseInfiniteTableQuery = {},
): UseInfiniteTableResult {
	return useHostedSdk().useInfiniteTable(name, query);
}

export function useFiles(prefix: string, options: UseFilesOptions = {}): UseFilesResult {
	return useHostedSdk().useFiles(prefix, options);
}

export function useUser() {
	return useHostedSdk().useUser();
}

export function useBranding(options: UseBrandingOptions = {}): UseBrandingResult {
	return useHostedSdk().useBranding(options);
}

export function useOrganizations(
	options: UseOrganizationsOptions = {},
): UseOrganizationsResult {
	return useHostedSdk().useOrganizations(options);
}

/** Promise methods retain the source SDK's same-origin defaults before a provider. */
export const tables = {
	get: (...args: Parameters<HostedSdk["tables"]["get"]>) => defaultSdk().then((sdk) => sdk.tables.get(...args)),
	insert: (...args: Parameters<HostedSdk["tables"]["insert"]>) => defaultSdk().then((sdk) => sdk.tables.insert(...args)),
	upsert: (...args: Parameters<HostedSdk["tables"]["upsert"]>) => defaultSdk().then((sdk) => sdk.tables.upsert(...args)),
	update: (...args: Parameters<HostedSdk["tables"]["update"]>) => defaultSdk().then((sdk) => sdk.tables.update(...args)),
	delete: (...args: Parameters<HostedSdk["tables"]["delete"]>) => defaultSdk().then((sdk) => sdk.tables.delete(...args)),
	query: (...args: Parameters<HostedSdk["tables"]["query"]>) => defaultSdk().then((sdk) => sdk.tables.query(...args)),
	count: (...args: Parameters<HostedSdk["tables"]["count"]>) => defaultSdk().then((sdk) => sdk.tables.count(...args)),
	subscribe: (...args: Parameters<HostedSdk["tables"]["subscribe"]>) => {
		let cancelled = false;
		let unsubscribe: (() => void) | undefined;
		void defaultSdk()
			.then((sdk) => {
				if (!cancelled) unsubscribe = sdk.tables.subscribe(...args);
			})
			.catch((cause: unknown) => {
				if (cancelled) return;
				args[2]({
					type: "error",
					message: cause instanceof Error ? cause.message : "Unable to load the Bifrost SDK.",
				});
			});
		return () => {
			cancelled = true;
			unsubscribe?.();
		};
	},
};

export const files = {
	read: (...args: Parameters<HostedSdk["files"]["read"]>) => defaultSdk().then((sdk) => sdk.files.read(...args)),
	readBytes: (...args: Parameters<HostedSdk["files"]["readBytes"]>) => defaultSdk().then((sdk) => sdk.files.readBytes(...args)),
	write: (...args: Parameters<HostedSdk["files"]["write"]>) => defaultSdk().then((sdk) => sdk.files.write(...args)),
	writeBytes: (...args: Parameters<HostedSdk["files"]["writeBytes"]>) => defaultSdk().then((sdk) => sdk.files.writeBytes(...args)),
	delete: (...args: Parameters<HostedSdk["files"]["delete"]>) => defaultSdk().then((sdk) => sdk.files.delete(...args)),
	list: (...args: Parameters<HostedSdk["files"]["list"]>) => defaultSdk().then((sdk) => sdk.files.list(...args)),
	exists: (...args: Parameters<HostedSdk["files"]["exists"]>) => defaultSdk().then((sdk) => sdk.files.exists(...args)),
	signedUrl: (...args: Parameters<HostedSdk["files"]["signedUrl"]>) => defaultSdk().then((sdk) => sdk.files.signedUrl(...args)),
	signedUrls: (...args: Parameters<HostedSdk["files"]["signedUrls"]>) => defaultSdk().then((sdk) => sdk.files.signedUrls(...args)),
	upload: (...args: Parameters<HostedSdk["files"]["upload"]>) => defaultSdk().then((sdk) => sdk.files.upload(...args)),
	download: (...args: Parameters<HostedSdk["files"]["download"]>) => defaultSdk().then((sdk) => sdk.files.download(...args)),
};
