import { useEffect } from "react";

interface FaviconState {
	defaultHref: string | null;
	defaultType: string | null;
	platformHref: string | null;
	appLogo: string | null;
}

// Keep state with the document's icon element, shared by platform branding and
// the app router. An app leaving must restore the latest branding, not a snapshot.
const faviconStates = new WeakMap<HTMLLinkElement, FaviconState>();

function favicon() {
	const link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
	if (!link) return null;
	let state = faviconStates.get(link);
	if (!state) {
		state = {
			defaultHref: link.getAttribute("href"),
			defaultType: link.getAttribute("type"),
			platformHref: null,
			appLogo: null,
		};
		faviconStates.set(link, state);
	}
	return { link, state };
}

function renderFavicon(link: HTMLLinkElement, state: FaviconState) {
	const customHref = state.appLogo || state.platformHref;
	const href = customHref || state.defaultHref;
	if (href === null) link.removeAttribute("href");
	else link.setAttribute("href", href);
	// Custom logos can be PNG, JPEG, or SVG; let the response determine the type.
	if (customHref || state.defaultType === null) link.removeAttribute("type");
	else link.setAttribute("type", state.defaultType);
}

/** Apply the saved square logo, including logos uploaded before this feature. */
export function setPlatformFavicon(squareLogoUrl: string | null | undefined) {
	const current = favicon();
	if (!current) return;
	let href: string | null = null;
	if (squareLogoUrl) {
		const url = new URL(squareLogoUrl, document.baseURI);
		// Logo URLs stay the same after replacement. Bypass cached old image bytes
		// whenever branding is loaded/refreshed, without requiring another upload.
		url.searchParams.set("v", String(Date.now()));
		href = url.href;
	}
	current.state.platformHref = href;
	renderFavicon(current.link, current.state);
}

/**
 * Drives the browser tab's "chrome" — the document <title> and the favicon —
 * from the currently-open entity (e.g. a Bifrost app), restoring the platform
 * icon and prior title on unmount or when the inputs change.
 *
 * The favicon is set from `logo`, which is an inline data URL
 * (`data:image/...;base64,...`) as served by the API's `logo` field. Because a
 * data URL carries its own MIME type, this works uniformly for SVG, PNG, and
 * JPEG logos without any format detection — we just drop the hardcoded `type`
 * attribute on the <link> so the data URL's own type drives it.
 *
 * When `logo` is null/empty the favicon is left untouched (the default Bifrost
 * or branded platform favicon stays). When `enabled` is false (e.g. embed mode,
 * where the host page owns its own chrome) the hook is a no-op.
 */
export function useDocumentChrome({
	title,
	logo,
	enabled = true,
}: {
	/** Tab title to set. Falsy values leave the title untouched. */
	title: string | null | undefined;
	/** Favicon source as a data URL. Falsy values leave the favicon untouched. */
	logo: string | null | undefined;
	/** When false, the hook does nothing. */
	enabled?: boolean;
}): void {
	useEffect(() => {
		if (!enabled || !title) return;
		const prev = document.title;
		document.title = title;
		return () => {
			document.title = prev;
		};
	}, [enabled, title]);

	useEffect(() => {
		if (!enabled || !logo) return;
		const current = favicon();
		if (!current) return;
		current.state.appLogo = logo;
		renderFavicon(current.link, current.state);
		return () => {
			current.state.appLogo = null;
			renderFavicon(current.link, current.state);
		};
	}, [enabled, logo]);
}
