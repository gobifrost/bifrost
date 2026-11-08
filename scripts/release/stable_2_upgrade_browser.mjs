#!/usr/bin/env node
/** Real browser proof for the immutable SDK2 upgrade fixture. */
import { chromium, request } from "playwright";
import { expect } from "@playwright/test";

const ORIGIN = "http://localhost";
const ACCESS_TOKEN_KEY = "bifrost_access_token";
const SOCKET_TIMEOUT_MS = 20_000;
const UI_TIMEOUT_MS = 20_000;
let phase = "input";
const diagnostic = { legacy_socket_opens: 0, legacy_table_acks: 0, browser_refreshes: 0, browser_refresh_status: 0, control_status: 0, page_errors: 0, live_value: "unobserved" };

class ProofFailure extends Error {}

function need(value, phase) {
	if (!value) throw new ProofFailure(phase);
	return value;
}

function string(value, phase) {
	if (typeof value !== "string" || value.length === 0) throw new ProofFailure(phase);
	return value;
}

function expiryMilliseconds(value) {
	if (!Number.isInteger(value) || value <= 0) throw new ProofFailure("input_expiry");
	return value * 1_000;
}

function isUnexpired(token) {
	try {
		const part = token.split(".")[1];
		const payload = part && JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
		return typeof payload?.exp === "number" && payload.exp * 1_000 > Date.now();
	} catch {
		return false;
	}
}

async function configFromStdin() {
	const chunks = [];
	for await (const chunk of process.stdin) chunks.push(chunk);
	let raw;
	try {
		raw = JSON.parse(Buffer.concat(chunks).toString("utf8"));
	} catch {
		throw new ProofFailure("input_json");
	}
	if (!raw || typeof raw !== "object") throw new ProofFailure("input_shape");
	const application = need(raw.application, "input_application");
	const liveTable = need(raw.live_table, "input_live_table");
	return {
		accessToken: string(raw.access_token, "input_access_token"),
		refreshToken: string(raw.refresh_token, "input_refresh_token"),
		controlAccessToken: string(raw.control_access_token, "input_control_access_token"),
		controlRefreshToken: string(raw.control_refresh_token, "input_control_refresh_token"),
		expiresAtMs: expiryMilliseconds(raw.expires_at),
		application: { slug: string(application.slug, "input_application_slug"), id: string(application.id, "input_application_id") },
		liveTable: {
			id: string(liveTable.id, "input_table_id"),
			documentId: string(liveTable.document_id, "input_document_id"),
			name: string(liveTable.name, "input_table_name"),
		},
		epochRoleId: string(raw.epoch_role_id, "input_epoch_role_id"),
	};
}

function bearer(token) {
	return { Authorization: `Bearer ${token}` };
}

async function run() {
	let browser;
	let context;
	let controlApi;
	let page;
	try {
		const config = await configFromStdin();
		const tableChannel = `table:${config.liveTable.id}`;
		const documentPath = `/api/tables/${encodeURIComponent(config.liveTable.id)}/documents/${encodeURIComponent(config.liveTable.documentId)}`;
		let controlAccess = config.controlAccessToken;
		let controlRefresh = config.controlRefreshToken;
		const queryTokenSockets = [];
		let browserRefreshes = 0;
		let pageErrors = 0;
		let initialSdk2QueryTokenVerified = false;
		let expiryRecovered = false;
		let epochRecovered = false;
		let localStatePreserved = false;
		let revokedFamilyRefused = false;
		let forbiddenRevokedDelivery = false;

		phase = "browser_start";
		browser = await chromium.launch({ headless: true });
		context = await browser.newContext();
		await context.addCookies([
			{ name: "access_token", value: config.accessToken, url: ORIGIN, httpOnly: true, secure: false, sameSite: "Lax" },
			{ name: "refresh_token", value: config.refreshToken, url: ORIGIN, httpOnly: true, secure: false, sameSite: "Lax" },
		]);
		await context.addInitScript((token) => localStorage.setItem("bifrost_access_token", token), config.accessToken);
		page = await context.newPage();
		page.on("pageerror", () => { pageErrors += 1; diagnostic.page_errors = pageErrors; });

		// These observers only record the narrow, non-secret facts the proof needs.
		page.on("response", (response) => {
			try {
				if (new URL(response.url()).pathname === "/api/auth/refresh") {
					diagnostic.browser_refresh_status = response.status();
					if (response.status() === 200) browserRefreshes += 1;
					diagnostic.browser_refreshes = browserRefreshes;
				}
			} catch {
				// Ignore unrelated browser responses without retaining their details.
			}
		});
		page.on("websocket", (socket) => {
			let url;
			try { url = new URL(socket.url()); } catch { return; }
			const token = url.pathname === "/ws/connect" ? url.searchParams.get("token") : null;
			if (!token) return;
			const record = { usesInitialToken: token === config.accessToken, tableAcknowledged: false, closed: false };
			queryTokenSockets.push(record);
			diagnostic.legacy_socket_opens = queryTokenSockets.length;
			socket.on("framereceived", (frame) => {
				if (typeof frame.payload !== "string") return;
				try {
					const message = JSON.parse(frame.payload);
					if (message?.type === "subscribed" && message.channel === tableChannel) {
						record.tableAcknowledged = true;
						diagnostic.legacy_table_acks = queryTokenSockets.filter((item) => item.tableAcknowledged).length;
					}
					if (message?.type === "document_change" && message.row?.value === 3) forbiddenRevokedDelivery = true;
				} catch {
					// Never retain or print WebSocket frames.
				}
			});
			socket.on("close", () => { record.closed = true; });
		});

		controlApi = await request.newContext({ baseURL: ORIGIN });
		const refreshControl = async () => {
			phase = "control_refresh";
			const response = await controlApi.post("/auth/refresh", { data: { refresh_token: controlRefresh } });
			expect(response.status()).toBe(200);
			const body = await response.json();
			controlAccess = string(body?.access_token, "control_refresh_access");
			controlRefresh = string(body?.refresh_token, "control_refresh_token");
		};
		const patchWithControl = async (value) => {
			const response = await controlApi.patch(documentPath, { headers: bearer(controlAccess), data: { data: { value } } });
			diagnostic.control_status = response.status();
			expect(response.status()).toBe(200);
		};
		const acknowledgements = () => queryTokenSockets.filter((socket) => socket.tableAcknowledged).length;

		phase = "initial_app";
		await page.goto(`${ORIGIN}/apps/${encodeURIComponent(config.application.slug)}`, { waitUntil: "domcontentloaded" });
		const liveValue = page.getByLabel("Live Value");
		phase = "initial_table_snapshot";
		await expect(liveValue).toHaveText("0", { timeout: UI_TIMEOUT_MS });
		phase = "initial_local_state";
		await page.getByRole("button", { name: "Local Count 0", exact: true }).click();
		await page.getByRole("button", { name: "Local Count 1", exact: true }).click();
		await expect(page.getByRole("button", { name: "Local Count 2", exact: true })).toBeVisible();
		phase = "initial_sdk2_handshake";
		await expect.poll(() => queryTokenSockets.some((socket) => socket.usesInitialToken && socket.tableAcknowledged), { timeout: SOCKET_TIMEOUT_MS }).toBe(true);
		initialSdk2QueryTokenVerified = true;
		const initialAcks = acknowledgements();

		phase = "expiry";
		const refreshesBeforeExpiry = browserRefreshes;
		await new Promise((resolve) => setTimeout(resolve, Math.max(0, config.expiresAtMs + 1_000 - Date.now())));

		phase = "expiry_table_update";
		await patchWithControl(1);
		phase = "expiry_visible_value";
		await expect(liveValue).toHaveText("1", { timeout: UI_TIMEOUT_MS });
		phase = "expiry_browser_refresh";
		await expect.poll(() => browserRefreshes, { timeout: SOCKET_TIMEOUT_MS }).toBeGreaterThan(refreshesBeforeExpiry);
		phase = "expiry_table_resubscription";
		await expect.poll(acknowledgements, { timeout: SOCKET_TIMEOUT_MS }).toBeGreaterThan(initialAcks);
		expiryRecovered = true;

		phase = "authority_epoch";
		const acksBeforeEpochUpdate = acknowledgements();
		const refreshesBeforeEpochUpdate = browserRefreshes;
		const roleResponse = await controlApi.put(`/api/roles/${encodeURIComponent(config.epochRoleId)}/permissions`, {
			headers: bearer(controlAccess), data: { permissions: [], runtime_permissions: ["workflows.execute"] },
		});
		diagnostic.control_status = roleResponse.status();
		expect(roleResponse.status()).toBe(200);
		await refreshControl();

		phase = "epoch_table_update";
		await patchWithControl(2);
		await expect(liveValue).toHaveText("2", { timeout: UI_TIMEOUT_MS });
		await expect(page.getByRole("button", { name: "Local Count 2", exact: true })).toBeVisible();
		await expect.poll(() => browserRefreshes, { timeout: SOCKET_TIMEOUT_MS }).toBeGreaterThan(refreshesBeforeEpochUpdate);
		await expect.poll(acknowledgements, { timeout: SOCKET_TIMEOUT_MS }).toBeGreaterThan(acksBeforeEpochUpdate);
		epochRecovered = true;
		localStatePreserved = true;

		phase = "browser_session_revoke";
		const browserAccess = string(await page.evaluate((key) => localStorage.getItem(key), ACCESS_TOKEN_KEY), "browser_access_token");
		if (!isUnexpired(browserAccess)) throw new ProofFailure("browser_access_expiry");
		const browserRefresh = string((await context.cookies(ORIGIN)).find((cookie) => cookie.name === "refresh_token")?.value, "browser_refresh_token");
		// controlApi owns control cookies after its refreshes, so carry the browser
		// family explicitly; the browser itself retains its now-revoked cookies.
		const logoutResponse = await controlApi.post("/auth/logout", { headers: bearer(browserAccess), data: { refresh_token: browserRefresh } });
		expect(logoutResponse.status()).toBe(200);

		phase = "revoked_table_update";
		await refreshControl();
		phase = "revoked_table_update";
		await patchWithControl(3);
		await expect.poll(() => queryTokenSockets.length > 0 && queryTokenSockets.every((socket) => socket.closed), { timeout: SOCKET_TIMEOUT_MS }).toBe(true);
		await expect(liveValue).not.toHaveText("3");
		expect(forbiddenRevokedDelivery).toBe(false);

		phase = "revoked_direct_api";
		const revokedRead = await controlApi.get(documentPath, { headers: bearer(browserAccess) });
		expect(revokedRead.status()).toBe(401);
		revokedFamilyRefused = true;
		expect(pageErrors).toBe(0);

		process.stdout.write(`${JSON.stringify({
			passed: true,
			initial_sdk2_query_token_verified: initialSdk2QueryTokenVerified,
			expiry_recovered: expiryRecovered,
			epoch_recovered: epochRecovered,
			local_state_preserved: localStatePreserved,
			revoked_family_refused: revokedFamilyRefused,
			browser_refreshes: browserRefreshes,
			table_subscription_acks: acknowledgements(),
			page_errors: pageErrors,
		})}\n`);
	} catch (error) {
		if (page && !page.isClosed()) {
			const text = await page.getByLabel("Live Value").textContent({ timeout: 500 }).catch(() => null);
			diagnostic.live_value = ["0", "1", "2", "3", "error", "loading", "missing"].includes(text) ? text : "absent";
		}
		throw error;
	} finally {
		await controlApi?.dispose();
		await context?.close();
		await browser?.close();
	}
}

try {
	await run();
} catch (error) {
	process.stderr.write(`stable_2_upgrade_browser failed phase=${phase} type=${error instanceof ProofFailure ? "assertion" : "runtime"} observations=${JSON.stringify(diagnostic)}\n`);
	process.exitCode = 1;
}
