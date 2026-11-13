import { RuleTester } from "eslint";
import tseslint from "typescript-eslint";
import { local } from "./eslint.config.js";

const tester = new RuleTester({
	languageOptions: {
		parser: tseslint.parser,
		parserOptions: { ecmaFeatures: { jsx: true } },
	},
});
const filename = "src/pages/Example.tsx";
const viewport = (classes) => ({
	filename,
	code: `<div className="${classes}" />`,
});

tester.run(
	"viewport-height-requires-scroll",
	local.rules["viewport-height-requires-scroll"],
	{
		valid: [
			viewport("flex h-screen items-center overflow-y-auto"),
			viewport("flex h-screen items-center overflow-scroll"),
			viewport("flex min-h-screen items-center"),
		],
		invalid: [
			"flex h-screen items-center",
			"flex h-screen items-center overflow-hidden",
			"flex h-screen items-center overflow-clip",
			"flex h-screen items-center overflow-x-auto",
			"flex h-screen items-center md:overflow-y-auto",
		].map((classes) => ({ ...viewport(classes), errors: 1 })),
	},
);

const dialogImport = 'import { Dialog } from "@/components/ui/dialog";';
tester.run("no-custom-overlay", local.rules["no-custom-overlay"], {
	valid: [
		{ filename, code: `${dialogImport} const view = <Dialog />;` },
		{ filename, code: '<div className="relative inset-0 z-50" />' },
		{
			filename: "src/components/ui/dialog.tsx",
			code: '<div className="fixed inset-0 z-50" />',
		},
	],
	invalid: [
		{ filename, code: '<div className="fixed inset-0 z-50" />', errors: 1 },
		{
			filename,
			code: `${dialogImport} const view = <><Dialog /><div className="fixed inset-0 z-50" /></>;`,
			errors: 1,
		},
	],
});
