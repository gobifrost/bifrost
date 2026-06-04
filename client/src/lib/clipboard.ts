/**
 * Copy text to the clipboard, working in both secure and insecure contexts.
 *
 * The async Clipboard API (`navigator.clipboard`) is only exposed in secure
 * contexts — HTTPS, or `localhost` / `127.0.0.1` over HTTP. When the dev or
 * preview server is reached over plain HTTP on any other host (an IP address or
 * a hostname like `http://devbox:5173`), `navigator.clipboard` is `undefined`
 * and every copy silently fails. In that case we fall back to the legacy
 * `document.execCommand("copy")` path, which works regardless of secure context.
 *
 * @returns `true` if the copy succeeded, `false` otherwise. Callers decide how
 * to surface success/failure (toast, button state, etc.).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
	try {
		if (navigator.clipboard?.writeText) {
			await navigator.clipboard.writeText(text);
			return true;
		}
	} catch {
		// Secure-context API exists but rejected (permissions, not focused,
		// etc.) — fall through to the legacy path.
	}

	return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
	if (typeof document === "undefined") return false;

	const textarea = document.createElement("textarea");
	textarea.value = text;
	textarea.setAttribute("readonly", "");
	// Keep it off-screen and inert so it doesn't scroll, flash, or steal layout.
	textarea.style.position = "fixed";
	textarea.style.top = "0";
	textarea.style.left = "0";
	textarea.style.opacity = "0";
	textarea.style.pointerEvents = "none";

	document.body.appendChild(textarea);

	// Preserve any selection we're about to clobber so we can restore it.
	const selection = document.getSelection();
	const previousRange =
		selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

	textarea.select();

	let succeeded = false;
	try {
		succeeded = document.execCommand("copy");
	} catch {
		succeeded = false;
	}

	document.body.removeChild(textarea);

	if (previousRange && selection) {
		selection.removeAllRanges();
		selection.addRange(previousRange);
	}

	return succeeded;
}
