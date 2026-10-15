import http from "node:http";
import net from "node:net";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { buildReport, markdown, validateLedger } from "./acceptance-report.mjs";
const target = { host: process.env.TEST_CLIENT_HOST || "client", port: 80 };
const localOrigins = [3000, 3001, 3002];

function createLocalOrigin(port) {
	const server = http.createServer((request, response) => {
		const upstream = http.request(
			{
				...target,
				method: request.method,
				path: request.url,
				headers: { ...request.headers, host: target.host },
			},
			(upstreamResponse) => {
				response.writeHead(
					upstreamResponse.statusCode ?? 502,
					upstreamResponse.headers,
				);
				upstreamResponse.pipe(response);
			},
		);
		upstream.on("error", (error) => {
			if (!response.headersSent) {
				response.writeHead(502, { "content-type": "text/plain" });
				response.end(`Client proxy failed: ${error.message}`);
			} else {
				response.destroy(error);
			}
		});
		request.on("error", () => upstream.destroy());
		response.on("error", () => upstream.destroy());
		request.pipe(upstream);
	});

	server.on("upgrade", (request, socket, head) => {
		const upstream = net.connect(target.port, target.host, () => {
			upstream.write(
				`${request.method} ${request.url} HTTP/${request.httpVersion}\r\n`,
			);
			for (const [name, value] of Object.entries(request.headers)) {
				if (value !== undefined) {
					upstream.write(
						`${name}: ${name === "host" ? target.host : value}\r\n`,
					);
				}
			}
			upstream.write("\r\n");
			if (head.length > 0) upstream.write(head);
			socket.pipe(upstream).pipe(socket);
		});
		upstream.on("error", () => socket.destroy());
		socket.on("error", () => upstream.destroy());
	});
	server.on("clientError", (_error, socket) => socket.destroy());

	return new Promise((resolve, reject) => {
		server.once("error", reject);
		server.listen(port, "127.0.0.1", () => resolve(server));
	});
}

// Run these fast checks in the same Node/container environment as browser CI.
// This keeps report joins covered by the existing gate.
const harnessChecks = spawnSync(
	process.execPath,
	["--test", "e2e/support/acceptance-report.test.mjs"],
	{ stdio: "inherit" },
);
if (harnessChecks.status !== 0) process.exit(harnessChecks.status ?? 1);

const servers = await Promise.all(localOrigins.map(createLocalOrigin));
const runStartedAt = Date.now();
const playwright = spawn(
	"npx",
	["playwright", "test", ...process.argv.slice(2)],
	{
		stdio: "inherit",
		env: process.env,
	},
);

const exitCode = await new Promise((resolve) => {
	playwright.once("exit", (code, signal) => {
		resolve(code ?? (signal ? 1 : 0));
	});
});
await Promise.all(
	servers.map(
		(server) =>
			new Promise((resolve, reject) =>
				server.close((error) => (error ? reject(error) : resolve())),
			),
	),
);
process.exitCode = exitCode;

// The ledger supplements the existing full HTML/JSON reports. Preserve a red
// browser result, and fail loudly if the coverage references become invalid.
try {
	const ledger = JSON.parse(
		readFileSync("e2e/acceptance-ledger.json", "utf8"),
	);
	validateLedger(ledger, (spec) => readFileSync(`e2e/${spec}`, "utf8"));
	const results = JSON.parse(
		readFileSync("playwright-results/results.json", "utf8"),
	);
	if (
		!results.stats?.startTime ||
		Date.parse(results.stats.startTime) < runStartedAt
	) {
		throw new Error(
			"No fresh Playwright JSON report was produced for this run",
		);
	}
	writeFileSync(
		"playwright-results/acceptance.md",
		markdown(buildReport(ledger, results)),
	);
} catch (error) {
	console.error("Could not produce acceptance report:", error);
	process.exitCode = 1;
}
