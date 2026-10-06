const { readFileSync } = require("node:fs");
const { spawn } = require("node:child_process");

const status = readFileSync(0, "utf8");
const baseURL = process.env.BIFROST_PREVIEW_URL;
const password = status.match(/^Login:\s+\S+\s+\/\s+(\S+)/m)?.[1];

if (!baseURL || !password) {
	throw new Error(
		"Expected a local debug-stack URL and debug.sh status on stdin with Login.",
	);
}

// Keep debug credentials in the spawned Playwright process only. Do not log
// the status input, construct a credential file, or pass secrets as CLI args.
const child = spawn(
	"npx",
	["playwright", "test", "-c", "playwright.preview.config.ts"],
	{
		stdio: "inherit",
		env: {
			...process.env,
			BIFROST_PREVIEW_URL: baseURL,
			BIFROST_PREVIEW_EMAIL: "dev@gobifrost.com",
			BIFROST_PREVIEW_PASSWORD: password,
		},
	},
);
child.once("exit", (code, signal) => {
	process.exitCode = code ?? (signal ? 1 : 0);
});
