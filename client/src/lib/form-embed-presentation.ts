export type FormEmbedTheme = "light" | "dark" | "system";

export interface FormEmbedPresentation {
	theme: FormEmbedTheme;
	showHeader: boolean;
	transparentBackground: boolean;
}

export const FORM_EMBED_PRESENTATION_PARAMS = new Set([
	"theme",
	"header",
	"background",
]);

export function parseFormEmbedPresentation(
	pathname: string,
	search: string,
): FormEmbedPresentation | null {
	if (!pathname.startsWith("/embedded/forms/")) return null;

	const params = new URLSearchParams(search);
	const requestedTheme = params.get("theme");
	const theme: FormEmbedTheme =
		requestedTheme === "dark" || requestedTheme === "system"
			? requestedTheme
			: "light";

	return {
		theme,
		showHeader: params.get("header") !== "false",
		transparentBackground: params.get("background") === "transparent",
	};
}

export function formRuntimeQueryParams(
	searchParams: URLSearchParams,
	isEmbed: boolean,
): Record<string, string> {
	const params: Record<string, string> = {};
	searchParams.forEach((value, key) => {
		if (!isEmbed || !FORM_EMBED_PRESENTATION_PARAMS.has(key)) {
			params[key] = value;
		}
	});
	return params;
}
