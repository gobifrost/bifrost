import {
	HOSTED_SDK_API_MAJOR,
	HOSTED_SDK_FACTORY_VERSION,
	type HostedSdkDescriptor,
	type HostedSdkFactory,
	type HostedSdkLoaderState,
} from "./hosted-contract";

const FINGERPRINT = /^[a-f0-9]{64}$/;

export class HostedSdkLoadError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "HostedSdkLoadError";
	}
}

function state(): HostedSdkLoaderState {
	if (typeof window === "undefined") {
		throw new HostedSdkLoadError("The hosted Bifrost SDK can only load in a browser.");
	}
	return (window.__BIFROST_APP_SDK_LOADER_V1__ ??= {
		selections: new Map(),
		readyBySelection: new Map(),
	});
}

export function hostedSdkOrigin(baseUrl: string): string {
	if (typeof window === "undefined") {
		throw new HostedSdkLoadError("The hosted Bifrost SDK can only load in a browser.");
	}
	try {
		return new URL(baseUrl || "/", window.location.href).origin;
	} catch {
		throw new HostedSdkLoadError("The Bifrost SDK base URL is invalid.");
	}
}

function hexDigestToIntegrity(fingerprint: string): string {
	const bytes = new Uint8Array(fingerprint.match(/.{2}/g)!.map((part) => Number.parseInt(part, 16)));
	let binary = "";
	for (const byte of bytes) binary += String.fromCharCode(byte);
	return `sha256-${btoa(binary)}`;
}

function validateDescriptor(value: unknown, origin: string): HostedSdkDescriptor {
	if (!value || typeof value !== "object") {
		throw new HostedSdkLoadError("The Bifrost SDK descriptor is invalid.");
	}
	const descriptor = value as Partial<HostedSdkDescriptor>;
	if (
		descriptor.api_major !== HOSTED_SDK_API_MAJOR ||
		descriptor.factory_version !== HOSTED_SDK_FACTORY_VERSION
	) {
		throw new HostedSdkLoadError("This app requires an unsupported Bifrost SDK binding version.");
	}
	if (
		typeof descriptor.factory_id !== "string" ||
		typeof descriptor.fingerprint !== "string" ||
		typeof descriptor.integrity !== "string" ||
		typeof descriptor.asset_url !== "string" ||
		!FINGERPRINT.test(descriptor.fingerprint) ||
		!FINGERPRINT.test(descriptor.factory_id)
	) {
		throw new HostedSdkLoadError("The Bifrost SDK descriptor is invalid.");
	}
	if (!descriptor.asset_url.startsWith("/") || descriptor.asset_url.startsWith("//")) {
		throw new HostedSdkLoadError("The Bifrost SDK asset URL is not a relative hosted SDK asset.");
	}
	if (descriptor.integrity !== hexDigestToIntegrity(descriptor.fingerprint)) {
		throw new HostedSdkLoadError("The Bifrost SDK descriptor failed integrity validation.");
	}
	let asset: URL;
	try {
		asset = new URL(descriptor.asset_url, origin);
	} catch {
		throw new HostedSdkLoadError("The Bifrost SDK asset URL is invalid.");
	}
	if (
		asset.origin !== origin ||
		asset.search ||
		asset.hash ||
		asset.pathname !== `/api/app-sdk/assets/${descriptor.fingerprint}.js`
	) {
		throw new HostedSdkLoadError("The Bifrost SDK asset URL is not a valid hosted SDK asset.");
	}
	return descriptor as HostedSdkDescriptor;
}

async function descriptorFor(origin: string): Promise<HostedSdkDescriptor> {
	let response: Response;
	try {
		response = await fetch(`${origin}/api/app-sdk/v1/manifest`, { cache: "no-store" });
	} catch {
		throw new HostedSdkLoadError("Unable to load the Bifrost SDK. Check the connection and reload the app.");
	}
	if (!response.ok) {
		throw new HostedSdkLoadError("Unable to load the Bifrost SDK. Check the connection and reload the app.");
	}
	return validateDescriptor(await response.json(), origin);
}

function appendFactoryScript(origin: string, descriptor: HostedSdkDescriptor): Promise<HostedSdkFactory> {
	const assetUrl = new URL(descriptor.asset_url, origin).href;
	return new Promise((resolve, reject) => {
		const script = document.createElement("script");
		script.src = assetUrl;
		script.integrity = descriptor.integrity;
		script.crossOrigin = "anonymous";
		script.async = true;
		const cleanup = () => script.remove();
		script.onload = () => {
			cleanup();
			const factory = window.__BIFROST_APP_SDK_FACTORIES_V1__?.[descriptor.factory_id];
			if (!factory || typeof factory.createSdk !== "function") {
				reject(new HostedSdkLoadError("The Bifrost SDK asset did not register a compatible factory."));
				return;
			}
			resolve(factory);
		};
		script.onerror = () => {
			cleanup();
			reject(new HostedSdkLoadError("Unable to load the Bifrost SDK. Check the connection and reload the app."));
		};
		document.head.appendChild(script);
	});
}

/** Load one immutable factory for this document. SDK instances stay per binding. */
export async function loadHostedSdkFactory(baseUrl: string): Promise<HostedSdkFactory> {
	const origin = hostedSdkOrigin(baseUrl);
	const cacheKey = `${origin}|${HOSTED_SDK_API_MAJOR}`;
	const shared = state();
	const cached = shared.selections.get(cacheKey);
	if (cached) return cached;
	const load = descriptorFor(origin)
		.then((descriptor) => appendFactoryScript(origin, descriptor))
		.then((factory) => {
			shared.readyBySelection.set(cacheKey, factory);
			return factory;
		})
		.catch((error: unknown) => {
			shared.selections.delete(cacheKey);
			shared.readyBySelection.delete(cacheKey);
			throw error;
		});
	shared.selections.set(cacheKey, load);
	return load;
}

/** Host mounting uses this before evaluating an App entry. */
export function preloadHostedSdkFactory(baseUrl: string): Promise<HostedSdkFactory> {
	return loadHostedSdkFactory(baseUrl);
}

/** A binding can synchronously reuse a factory a host has already preloaded. */
export function getPreloadedHostedSdkFactory(baseUrl: string): HostedSdkFactory | undefined {
	const origin = hostedSdkOrigin(baseUrl);
	return state().readyBySelection.get(`${origin}|${HOSTED_SDK_API_MAJOR}`);
}
