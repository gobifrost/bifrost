import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import { plugin as shadcn } from "@shadcn/lint";

/**
 * Local design-governance rules, mapped from upstream review feedback:
 * - one-primary-action: one decisive action per surface (DESIGN.md composition).
 * - require-motion-reduce: every animation declares reduced-motion behavior.
 * - viewport-height-requires-scroll: full-viewport containers must scroll.
 * - no-custom-overlay: reuse Dialog/Sheet/Popover instead of fixed overlays.
 * - no-fixed-width-without-max: fixed widths must survive mobile viewports.
 */
function staticClassString(node) {
	const value = node.value;
	if (!value) return null;
	if (value.type === "Literal" && typeof value.value === "string")
		return value.value;
	if (
		value.type === "JSXExpressionContainer" &&
		value.expression.type === "TemplateLiteral" &&
		value.expression.expressions.length === 0
	)
		return value.expression.quasis.map((q) => q.value.cooked).join("");
	return null;
}

export const local = {
	rules: {
		"one-primary-action": {
			meta: {
				type: "suggestion",
				docs: {
					description:
						"One primary action per surface; secondary actions use outline/secondary/ghost variants.",
				},
			},
			create(context) {
				const filename = context.filename ?? context.getFilename?.() ?? "";
				if (
					filename.includes("src/components/ui/") ||
					filename.endsWith(".test.tsx")
				)
					return {};
				// Competing primaries share one surface: count default-variant
				// Buttons per JSX parent, skipping conditional branches (only
				// one branch renders) and distinct surfaces.
				const counts = new Map();
				function isDefaultButton(node) {
					if (node.name.type !== "JSXIdentifier") return false;
					if (node.name.name !== "Button") return false;
					const variant = node.attributes.find(
						(a) =>
							a.type === "JSXAttribute" &&
							a.name.type === "JSXIdentifier" &&
							a.name.name === "variant",
					);
					if (!variant) return true;
					return (
						variant.value?.type === "Literal" &&
						variant.value.value === "default"
					);
				}
				return {
					JSXOpeningElement(node) {
						if (!isDefaultButton(node)) return;
						// Ascend from this Button's own JSXElement to the
						// enclosing surface. Crossing a conditional means
						// exclusive branches, not competitors.
						let current = node.parent?.parent;
						let crossedConditional = false;
						while (
							current &&
							current.type !== "JSXElement" &&
							current.type !== "JSXFragment"
						) {
							if (
								current.type === "ConditionalExpression" ||
								current.type === "LogicalExpression"
							)
								crossedConditional = true;
							current = current.parent;
						}
						if (!current || crossedConditional) return;
						const seen = (counts.get(current) ?? 0) + 1;
						counts.set(current, seen);
						if (seen > 1)
							context.report({
								node,
								message:
									"Competing primary <Button> on one surface. Keep one default variant; use variant=\"outline\"/\"secondary\"/\"ghost\" for the rest.",
							});
					},
				};
			},
		},
		"require-motion-reduce": {
			meta: {
				type: "suggestion",
				docs: {
					description:
						"Every animate-* class must declare reduced-motion behavior via motion-reduce: or motion-safe:.",
				},
			},
			create(context) {
				return {
					JSXAttribute(node) {
						if (node.name.type !== "JSXIdentifier") return;
						if (node.name.name !== "className") return;
						const cls = staticClassString(node);
						if (!cls || !cls.includes("animate-")) return;
						if (cls.includes("motion-reduce:") || cls.includes("motion-safe:"))
							return;
						context.report({
							node,
							message:
								"Animation without reduced-motion handling. Add motion-reduce:animate-none (or scope it with motion-safe:).",
						});
					},
				};
			},
		},
		"viewport-height-requires-scroll": {
			meta: {
				type: "suggestion",
				docs: {
					description:
						"Centered viewport-height containers must own scrolling via overflow-* so short screens can reach clipped content.",
				},
			},
			create(context) {
				return {
					JSXAttribute(node) {
						if (node.name.type !== "JSXIdentifier") return;
						if (node.name.name !== "className") return;
						const cls = staticClassString(node);
						if (!cls) return;
						// Only fixed heights clip: min-h-* grows with content, but
						// a fixed viewport height pins the box, so centering can
						// push the top past the scroll origin with no way back.
						// The (^|\s|:) guard keeps min-h-/max-h- out.
						const viewportHeight = /(^|\s|:)(h-screen|h-dvh|h-svh|h-lvh|h-\[100vh\]|h-\[100dvh\]|h-\[100svh\]|h-\[100lvh\])(\s|$)/.test(
							cls,
						);
						// The unreachable-content bug needs centering: items-center
						// pins the block middle, so overflow above the fold has no
						// scroll origin. Plain top-flow shells scroll via the body.
						const centered =
							cls.includes("items-center") ||
							cls.includes("justify-center") ||
							cls.includes("place-content-center");
						const scrollsVertically =
							/(^|\s)overflow(?:-y)?-(auto|scroll)(\s|$)/.test(cls);
						if (!viewportHeight || !centered || scrollsVertically)
							return;
						context.report({
							node,
							message:
								"Centered viewport-height container without overflow-*. On short screens the top clips with no scroll origin. Add overflow-y-auto.",
						});
					},
				};
			},
		},
		"no-custom-overlay": {
			meta: {
				type: "suggestion",
				docs: {
					description:
						"Reuse Dialog/Sheet/Popover instead of hand-built fixed overlays.",
				},
			},
			create(context) {
				const filename = context.filename ?? context.getFilename?.() ?? "";
				if (filename.includes("src/components/ui/")) return {};
				return {
					JSXOpeningElement(node) {
						if (node.name.type !== "JSXIdentifier") return;
						if (!/^[a-z]/.test(node.name.name)) return;
						const classAttr = node.attributes.find(
							(a) =>
								a.type === "JSXAttribute" &&
								a.name.type === "JSXIdentifier" &&
								a.name.name === "className",
						);
						if (!classAttr) return;
						const cls = staticClassString(classAttr);
						if (!cls) return;
						const isOverlay =
							/(^|\s)fixed(\s|$)/.test(cls) &&
							cls.includes("inset-0") &&
							/(^|\s|:)z-\d/.test(cls);
						if (!isOverlay) return;
						context.report({
							node,
							message:
								"Hand-built fixed overlay. Reuse Dialog, Sheet, or Popover so focus, Escape, and mobile behavior stay consistent.",
						});
					},
				};
			},
		},
		"no-fixed-width-without-max": {
			meta: {
				type: "suggestion",
				docs: {
					description:
						"Container-scale fixed widths must carry a mobile survival strategy (max-w-*, viewport-relative width, or responsive variant).",
				},
			},
			create(context) {
				return {
					JSXAttribute(node) {
						if (node.name.type !== "JSXIdentifier") return;
						if (node.name.name !== "className") return;
						const cls = staticClassString(node);
						if (!cls || !cls.includes("w-[")) return;
						if (
							cls.includes("max-w-") ||
							cls.includes("vw") ||
							cls.includes("%") ||
							cls.includes("sm:") ||
							cls.includes("md:") ||
							cls.includes("lg:") ||
							cls.includes("xl:") ||
							cls.includes("2xl:")
						)
							return;
						// Table-owned scrollers manage their own columns; min-widths
						// are the correct way to force a scroll region.
						const opening = node.parent;
						const elementName =
							opening?.name?.type === "JSXIdentifier"
								? opening.name.name
								: "";
						if (
							/^(DataTableHead|DataTableCell|TableHead|TableCell|th|td|col)$/.test(
								elementName,
							)
						)
							return;
						// Ignore min-/max- widths; only bare w-[...] constrains.
						const withoutMinMax = cls
							.replace(/(?:min|max)-w-\[[^\]]*\]/g, "")
							.replace(/(?:min|max)-w-\([^)]*\)/g, "");
						const match = withoutMinMax.match(/w-\[([^\]]*)]/);
						if (!match) return;
						// Icon/logo-scale widths are not layout widths.
						const size = match[1].match(/^([\d.]+)\s*(px|rem|em)$/);
						if (size) {
							const value =
								parseFloat(size[1]) *
								(size[2] === "px" ? 1 : 16);
							if (value < 120) return;
						} else if (!/(px|rem|em|ch|cap)/.test(match[1])) {
							return;
						}
						context.report({
							node,
							message:
								"Fixed width without a mobile strategy. Add max-w-*, use a viewport-relative width, or scope it with sm:/md:.",
						});
					},
				};
			},
		},
	},
};

export default tseslint.config(
	{ ignores: ["dist"] },
	{
		extends: [js.configs.recommended, ...tseslint.configs.recommended],
		files: ["**/*.{ts,tsx}"],
		languageOptions: {
			ecmaVersion: 2020,
			globals: globals.browser,
		},
		plugins: {
			"react-hooks": reactHooks,
			"react-refresh": reactRefresh,
			shadcn,
			local,
		},
		settings: {
			shadcn: {
				note: "See DESIGN.md for design rules and approved exceptions.",
			},
		},
		rules: {
			...reactHooks.configs.recommended.rules,
			"react-refresh/only-export-components": "off",
			"no-console": ["warn", { allow: ["warn", "error"] }], // Allow console.warn and console.error, but warn about console.log
			// Allow underscore-prefixed unused variables
			"@typescript-eslint/no-unused-vars": [
				"error",
				{
					argsIgnorePattern: "^_",
					varsIgnorePattern: "^_",
				},
			],
			"shadcn/no-restyle": [
				"warn",
				{
					allow: ["layout"],
					contracts: [
						{
							pattern: "^CardTitle$",
							allow: ["layout", "typography"],
						},
						{
							pattern: "(Content|Header|Footer|Group|Panel)$",
							allow: ["layout", "spacing"],
						},
					],
				},
			],
			"shadcn/no-raw-colors": "error",
			"shadcn/no-arbitrary-values": ["warn", { allow: ["layout"] }],
			"shadcn/no-inline-styles": "warn",
			"shadcn/require-static-classes": "warn",
			"shadcn/no-unknown-classes": "error",
			"local/one-primary-action": "error",
			"local/require-motion-reduce": "error",
			"local/viewport-height-requires-scroll": "error",
			"local/no-custom-overlay": "error",
			"local/no-fixed-width-without-max": "error",
		},
	},
	{
		files: ["src/components/ui/**"],
		rules: {
			"shadcn/no-restyle": "off",
			"shadcn/no-arbitrary-values": "off",
			"shadcn/require-static-classes": "off",
		},
	},
	{
		// Test fixtures intentionally use fake classes (e.g. passthrough
		// assertions); the existence check does not apply to them.
		files: ["**/*.test.{ts,tsx}"],
		rules: {
			"shadcn/no-unknown-classes": "off",
		},
	},
	{
		// Semantic hooks with plain-CSS or test-anchor roles live outside
		// the Tailwind theme build. Listed here so they stay intentional.
		files: [
			"src/components/agents/AgentOverviewTab.tsx",
			"src/components/agents/AgentRunsTab.tsx",
		],
		rules: {
			"shadcn/no-unknown-classes": ["warn", { allow: ["agent-*"] }],
		},
	},
	{
		files: ["src/components/integrations/IntegrationSetupNotes.tsx"],
		rules: {
			"shadcn/no-unknown-classes": [
				"warn",
				{
					allow: [
						"integration-setup-notes",
						"integration-setup-notes-empty",
						"integration-setup-notes-content",
						"integration-setup-notes-container",
						"integration-setup-notes-context",
						"integration-setup-notes-work",
					],
				},
			],
		},
	},
	{
		files: ["src/pages/Home.tsx"],
		rules: {
			"shadcn/no-unknown-classes": ["warn", { allow: ["home-search"] }],
		},
	},
	{
		files: ["src/components/dependencies/DependencyGraph.tsx"],
		rules: {
			"shadcn/no-unknown-classes": [
				"warn",
				{ allow: ["bf-dependency-graph"] },
			],
		},
	},
	{
		// Customer-app runtime: dynamic styles and injected CSS are the product,
		// not design-system UI. Exempt from class-governance rules.
		files: ["src/lib/app-sdk/**"],
		rules: {
			"shadcn/no-restyle": "off",
			"shadcn/no-raw-colors": "off",
			"shadcn/no-arbitrary-values": "off",
			"shadcn/no-inline-styles": "off",
			"shadcn/require-static-classes": "off",
			"shadcn/no-unknown-classes": "off",
		},
	},
	{
		// Plain-CSS companions (not Tailwind utilities) that the linter cannot
		// see through the Tailwind theme build.
		files: ["src/components/execution/**", "src/pages/ExecutionDetails.tsx"],
		rules: {
			"shadcn/no-unknown-classes": [
				"warn",
				{
					allow: ["execution-detail-card", "execution-live-row", "execution-shell"],
				},
			],
		},
	},
);
