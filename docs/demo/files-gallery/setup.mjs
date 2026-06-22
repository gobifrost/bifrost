import { readFileSync } from "node:fs";

const BASE = "http://localhost:34212";
const SLUG = "gallery";
const NAME = "Shared Gallery";
const log = (...a) => console.log("[setup]", ...a);

async function login(username, password = "password") {
	const r = await fetch(`${BASE}/api/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/x-www-form-urlencoded" },
		body: `username=${encodeURIComponent(username)}&password=${password}`,
	});
	const d = await r.json();
	return d.access_token || null;
}

const tok = await login("dev@gobifrost.com");
if (!tok) throw new Error("admin login failed");
const H = (t = tok) => ({ Authorization: `Bearer ${t}`, "Content-Type": "application/json" });

async function api(method, path, body, t = tok) {
	const r = await fetch(`${BASE}${path}`, { method, headers: H(t), body: body ? JSON.stringify(body) : undefined });
	const txt = await r.text();
	let json; try { json = JSON.parse(txt); } catch { json = txt; }
	return { ok: r.ok, status: r.status, json };
}

async function putPolicy(prefix, rules, location = "workspace", scope) {
	const qs = new URLSearchParams({ location, ...(scope ? { scope } : {}) });
	const r = await api("PUT", `/api/files/policies/${encodeURIComponent(prefix)}?${qs}`, { policies: { policies: rules } });
	if (!r.ok) throw new Error(`putPolicy ${location}/${prefix} scope=${scope}: ${r.status} ${JSON.stringify(r.json)}`);
	log("policy:", location, prefix, "scope=", scope ?? "(caller)");
}

async function ensureOrg(name) {
	const list = await api("GET", "/api/organizations");
	const orgs = list.json.organizations || list.json.items || (Array.isArray(list.json) ? list.json : []);
	const found = orgs.find((o) => o.name === name);
	if (found) return found.id;
	const c = await api("POST", "/api/organizations", { name });
	if (!c.ok) throw new Error(`create org ${name}: ${JSON.stringify(c.json)}`);
	log("created org", name, c.json.id);
	return c.json.id;
}

async function ensureUser(email, orgId) {
	const c = await api("POST", "/api/users", { email, password: "password", organization_id: orgId, is_superuser: false, name: email.split("@")[0] });
	if (c.ok) { log("created user", email, "in org", orgId); return c.json.id; }
	if (c.status === 409) { log("user exists", email); return null; }
	throw new Error(`create user ${email}: ${JSON.stringify(c.json)}`);
}

// ---- 1. Orgs + users -------------------------------------------------------
const orgA = await ensureOrg("Gallery Org A");
const orgB = await ensureOrg("Gallery Org B");
await ensureUser("alice@gallery.example.com", orgA);
await ensureUser("bob@gallery.example.com", orgB);
log("orgA", orgA, "orgB", orgB);

// ---- 2. App ----------------------------------------------------------------
let appId;
const findApp = async () => {
	const list = await api("GET", "/api/applications");
	return (list.json.applications || []).find((a) => a.slug === SLUG);
};
const prior = await findApp();
if (prior) {
	appId = prior.id;
	log("reusing existing app", appId);
} else {
	const created = await api("POST", "/api/applications", {
		name: NAME, slug: SLUG, app_model: "inline_v1", organization_id: null, access_level: "authenticated", role_ids: [],
	});
	if (created.ok) { appId = created.json.id; log("created app", appId); }
	else {
		// slug may be reserved by a soft-deleted row — re-fetch.
		const again = await findApp();
		if (!again) throw new Error("create app: " + JSON.stringify(created.json));
		appId = again.id; log("recovered existing app", appId);
	}
}

// ---- 3. Policies -----------------------------------------------------------
// app source (admin)
await putPolicy(`apps/${SLUG}`, [{ name: "admin_app_source", actions: ["read", "write", "delete", "list"], when: { user: "is_platform_admin" } }], "workspace");
// shared/gallery GLOBAL policy — cascades to every org: everyone read/list, authed write/delete.
await putPolicy("gallery", [
	{ name: "everyone-read", description: "Anyone may browse.", actions: ["read", "list"], when: null },
	{ name: "authed-write", description: "Logged-in users upload/delete.", actions: ["write", "delete"], when: null },
], "shared");
// Org B OVERRIDE: read-only (proves org override beats the global write grant for org B).
await putPolicy("gallery", [
	{ name: "orgb-readonly", description: "Org B is read-only here.", actions: ["read", "list"], when: null },
], "shared", orgB);

// ---- 4. App source + publish ----------------------------------------------
const enc = (s) => Buffer.from(s, "utf-8").toString("base64");
for (const [rel, file] of [
	[`apps/${SLUG}/_layout.tsx`, "/tmp/files-drive/gallery_layout.tsx"],
	[`apps/${SLUG}/pages/index.tsx`, "/tmp/files-drive/gallery_index.tsx"],
]) {
	const w = await api("POST", "/api/files/write", { path: rel, content: enc(readFileSync(file, "utf-8")), mode: "cloud", location: "workspace", binary: true });
	if (!w.ok) throw new Error(`write ${rel}: ${JSON.stringify(w.json)}`);
	log("wrote", rel);
}
const pub = await api("POST", `/api/applications/${appId}/publish`, { message: "gallery demo" });
log("publish", pub.status);

// ---- 5. Seed files into all three trees: global, orgA, orgB ---------------
async function seed(name, bytes, contentType, scope, asTok = tok) {
	// Write server-side (no host→seaweedfs round-trip the presigned URL needs).
	const w = await api("POST", "/api/files/write", {
		path: `gallery/${name}`, content: Buffer.from(bytes).toString("base64"),
		mode: "cloud", location: "shared", binary: true, scope,
	}, asTok);
	log("  seeded", scope ?? "(caller)", `gallery/${name}`, "write", w.status, w.ok ? "" : JSON.stringify(w.json));
	return w.ok;
}
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const txt = (s) => Buffer.from(s);
// global pool (admin, scope=global)
await seed("global-welcome.png", png, "image/png", "global");
await seed("global-readme.txt", txt("This file lives in shared/global/gallery — the cross-org pool.\n"), "text/plain", "global");
// org A pool (as admin targeting orgA explicitly)
await seed("orgA-photo.png", png, "image/png", orgA);
await seed("orgA-note.txt", txt("Only Org A users see this (shared/<orgA>/gallery).\n"), "text/plain", orgA);
// org B pool
await seed("orgB-photo.png", png, "image/png", orgB);

log("");
log("DONE.");
log("App URL:        ", `${BASE}/apps/${SLUG}`);
log("Admin:          dev@gobifrost.com / password   (sees its OWN org's gallery by default; global via scope)");
log("Alice (Org A):  alice@gallery.example.com / password  (org A gallery, can upload)");
log("Bob (Org B):    bob@gallery.example.com / password    (org B gallery, READ-ONLY via override)");
