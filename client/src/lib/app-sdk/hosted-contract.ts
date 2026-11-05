import type * as React from "react";
import type * as JsxRuntime from "react/jsx-runtime";
import type { components } from "@/lib/v1";

import type * as Errors from "./errors";

/** The binding/factory interface negotiated independently from SDK wire APIs. */
export const HOSTED_SDK_API_MAJOR = 1;
export const HOSTED_SDK_FACTORY_VERSION = 1;

export interface HostedSdkPeers {
	react: typeof React;
	jsxRuntime: typeof JsxRuntime;
	errors: typeof Errors;
}

/** The hosted implementation preserves the existing installable SDK surface. */
export type HostedSdk = typeof import("./index.v2");

export interface HostedSdkFactory {
	createSdk(peers: HostedSdkPeers): HostedSdk;
}

export type HostedSdkDescriptor = components["schemas"]["HostedAppSdkDescriptor"];

declare global {
	interface Window {
		/** Immutable hosted factories, registered by their classic static script. */
		__BIFROST_APP_SDK_FACTORIES_V1__?: Record<string, HostedSdkFactory>;
		/** Shared loader state lets the host preload work for an App's binding copy. */
		__BIFROST_APP_SDK_LOADER_V1__?: HostedSdkLoaderState;
	}
}

export interface HostedSdkLoaderState {
	selections: Map<string, Promise<HostedSdkFactory>>;
	readyBySelection: Map<string, HostedSdkFactory>;
}
