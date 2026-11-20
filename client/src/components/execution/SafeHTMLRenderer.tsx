import { usePresentationStore } from "@/stores/presentationStore";
/** Renders report HTML in an opaque-origin frame so report CSS and scripts
 * cannot modify the surrounding application. Rich report scripts can run
 * inside the frame; forms, popups and top navigation remain sandboxed. */

import { useState } from "react";
import { cn } from "@/lib/utils";
import DOMPurify from "dompurify";
import { Button } from "@/components/ui/button";
import { ExternalLink } from "lucide-react";

interface SafeHTMLRendererProps {
	html: string;
	title?: string;
	className?: string;
}

export function SafeHTMLRenderer({
	html,
	title = "Execution result",
	className = "",
}: SafeHTMLRendererProps) {
	const [popupError, setPopupError] = useState(false);
	// Sanitization limits markup; the opaque-origin sandbox is the isolation boundary.
	const sanitizedHTML = DOMPurify.sanitize(html, {
		ADD_TAGS: ["script"],
		ADD_ATTR: ["onclick", "onload", "onerror"],
		ALLOWED_URI_REGEXP:
			/^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp|data|blob):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
		KEEP_CONTENT: true,
		WHOLE_DOCUMENT: true,
		FORBID_ATTR: [
			"onabort",
			"onblur",
			"onchange",
			"onfocus",
			"oninput",
			"onkeydown",
			"onkeypress",
			"onkeyup",
			"onmousedown",
			"onmousemove",
			"onmouseout",
			"onmouseover",
			"onmouseup",
			"onreset",
			"onselect",
			"onsubmit",
			"onunload",
		],
		ALLOWED_TAGS: [
			"html",
			"head",
			"body",
			"title",
			"meta",
			"link",
			"style",
			"script",
			"h1",
			"h2",
			"h3",
			"h4",
			"h5",
			"h6",
			"p",
			"div",
			"span",
			"br",
			"hr",
			"strong",
			"em",
			"b",
			"i",
			"u",
			"s",
			"small",
			"mark",
			"del",
			"ins",
			"sub",
			"sup",
			"ul",
			"ol",
			"li",
			"dl",
			"dt",
			"dd",
			"table",
			"thead",
			"tbody",
			"tfoot",
			"tr",
			"th",
			"td",
			"caption",
			"colgroup",
			"col",
			"blockquote",
			"pre",
			"code",
			"kbd",
			"samp",
			"var",
			"a",
			"img",
			"figure",
			"figcaption",
			"details",
			"summary",
			"section",
			"article",
			"aside",
			"header",
			"footer",
			"nav",
			"main",
			"button",
			"input",
			"select",
			"option",
			"textarea",
			"label",
			"form",
			"svg",
			"path",
			"circle",
			"rect",
			"line",
			"polygon",
			"polyline",
			"ellipse",
			"g",
			"defs",
			"use",
		],
		ALLOWED_ATTR: [
			"id",
			"class",
			"style",
			"title",
			"alt",
			"src",
			"href",
			"width",
			"height",
			"colspan",
			"rowspan",
			"align",
			"valign",
			"lang",
			"dir",
			"aria-label",
			"aria-labelledby",
			"aria-describedby",
			"aria-hidden",
			"role",
			"tabindex",
			"charset",
			"content",
			"name",
			"rel",
			"type",
			"integrity",
			"crossorigin",
			"onclick",
			"onload",
			"viewBox",
			"fill",
			"stroke",
			"stroke-width",
			"stroke-linecap",
			"stroke-linejoin",
			"d",
			"cx",
			"cy",
			"r",
			"x",
			"y",
			"x1",
			"y1",
			"x2",
			"y2",
			"points",
			"clip-rule",
			"fill-rule",
			"transform",
		],
	});

	const documentHTML = (() => {
		const doc = new DOMParser().parseFromString(sanitizedHTML, "text/html");
		if (!doc.querySelector('meta[name="viewport"]')) {
			const viewport = doc.createElement("meta");
			viewport.name = "viewport";
			viewport.content = "width=device-width, initial-scale=1";
			doc.head.prepend(viewport);
		}
		// Report-specific styles follow these low-specificity defaults.
		const defaults = doc.createElement("style");
		defaults.textContent = ":where(body){margin:16px;font-family:system-ui,sans-serif;line-height:1.5;overflow-wrap:anywhere}:where(img,svg,video){max-width:100%;height:auto}";
		doc.head.prepend(defaults);
		return "<!doctype html>" + doc.documentElement.outerHTML;
	})();

	const presentationMode = usePresentationStore((state) => state.enabled);
	const openInNewWindow = () => {
		setPopupError(false);
		const newWindow = window.open("", "_blank");
		if (newWindow) {
			newWindow.opener = null;
			newWindow.document.title = title;
			const frame = newWindow.document.createElement("iframe");
			frame.title = title;
			frame.setAttribute("sandbox", "allow-scripts");
			frame.referrerPolicy = "no-referrer";
			frame.srcdoc = documentHTML;
			frame.style.cssText = "position:fixed;inset:0;width:100%;height:100%;border:0;background:white";
			newWindow.document.body.replaceChildren(frame);
		} else {
			setPopupError(true);
		}
	};

	return (
		<div className={cn("min-w-0 space-y-3", className)}>
			<div className="flex flex-wrap items-center justify-end gap-2">
				<Button
					variant="outline"
					className="min-h-11"
					onClick={openInNewWindow}
					disabled={presentationMode}
					title="Open in new window"
				>
					<ExternalLink className="h-4 w-4" />
					<span>Open full result</span>
				</Button>
			</div>

			{popupError && (
				<p
					role="alert"
					className="rounded-[var(--bf-radius-control)] bg-[var(--bf-warning-soft)] p-3 text-sm"
				>
					The new window was blocked. Allow popups for this site and
					try again, or continue reading below.
				</p>
			)}
			<div data-pii-surface>
				<iframe
					title={title}
					sandbox="allow-scripts"
					referrerPolicy="no-referrer"
					srcDoc={documentHTML}
					className="block h-[min(70vh,700px)] w-full min-w-0 rounded-[var(--bf-radius-surface)] border bg-white"
				/>
			</div>
		</div>
	);
}
