import type { Page } from "@playwright/test";
/** Serialize one-use MFA proofs across the setup and browser worker processes. */
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout as delay } from "node:timers/promises";
import { generateTOTP } from "./totp";

export interface RecoveryProofCredentials {
	userId: string;
	totpSecret: string;
	recoveryCodes: string[];
	mfaSetupCounter: number;
}

type ProofState = { codes: string[]; lastTotpCounter: number };
type Regenerate = (accessToken: string, code: string) => Promise<string[]>;

async function regenerate(
	accessToken: string,
	code: string,
): Promise<string[]> {
	const response = await fetch(
		`${process.env.TEST_API_URL || "http://api:8000"}/auth/mfa/recovery-codes/regenerate`,
		{
			method: "POST",
			headers: {
				Authorization: `Bearer ${accessToken}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({ mfa_code: code }),
		},
	);
	if (!response.ok)
		throw new Error(
			`Recovery proof replenishment failed: ${response.status}`,
		);
	return (await response.json()).recovery_codes;
}

export class RecoveryProofPool {
	constructor(
		private directory: string,
		private replenish: Regenerate = regenerate,
	) {}

	async run(
		credentials: RecoveryProofCredentials,
		submit: (code: string) => Promise<string>,
	): Promise<string> {
		await mkdir(this.directory, { recursive: true, mode: 0o700 });
		const key = createHash("sha256")
			.update(credentials.userId + credentials.totpSecret)
			.digest("hex");
		const file = resolve(this.directory, `${key}.json`);
		const lock = `${file}.lock`;
		// Atomic mkdir coordinates different Playwright worker processes, not
		// just promises inside one worker. The lock covers proof consumption
		// and replenishment so rotation cannot invalidate an in-flight code.
		for (;;) {
			try {
				await mkdir(lock);
				break;
			} catch (error) {
				if ((error as NodeJS.ErrnoException).code !== "EEXIST")
					throw error;
				await delay(10);
			}
		}
		try {
			let state: ProofState;
			try {
				state = JSON.parse(await readFile(file, "utf8"));
			} catch (error) {
				if ((error as NodeJS.ErrnoException).code !== "ENOENT")
					throw error;
				state = {
					codes: [...credentials.recoveryCodes],
					lastTotpCounter: credentials.mfaSetupCounter,
				};
			}
			const save = async () => {
				await writeFile(`${file}.next`, JSON.stringify(state), {
					mode: 0o600,
				});
				await rename(`${file}.next`, file);
			};
			const code = state.codes.shift();
			if (!code) throw new Error("MFA recovery proof pool is exhausted");
			// Reserve before the request: an interrupted request must never
			// cause the same one-use proof to be submitted again.
			await save();
			const accessToken = await submit(code);
			if (!state.codes.length) {
				let current = Math.floor(Date.now() / 30000);
				if (state.lastTotpCounter > current) {
					await delay(
						state.lastTotpCounter * 30000 - Date.now() + 200,
					);
					current = Math.floor(Date.now() / 30000);
				}
				const counter = Math.max(current, state.lastTotpCounter + 1);
				// The test stack uses the normal +/- one-step login window.
				state.codes = await this.replenish(
					accessToken,
					generateTOTP(credentials.totpSecret, counter * 30000),
				);
				state.lastTotpCounter = counter;
				await save();
			}
			return accessToken;
		} finally {
			await rm(lock, { recursive: true });
		}
	}
}

const pool = new RecoveryProofPool(resolve(tmpdir(), "bifrost-e2e-mfa-proofs"));
export const withRecoveryProof = (
	credentials: RecoveryProofCredentials,
	submit: (code: string) => Promise<string>,
) => pool.run(credentials, submit);

/** Complete the normal MFA page with one uniquely reserved recovery proof. */
export async function completeBrowserMfa(
	page: Page,
	credentials: RecoveryProofCredentials,
): Promise<void> {
	await withRecoveryProof(credentials, async (code) => {
		const verified = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === "/auth/mfa/login" &&
				r.request().method() === "POST",
		);
		await page.getByLabel(/code|totp|verification/i).fill(code);
		await page
			.getByRole("button", { name: /verify|submit|continue/i })
			.click();
		const result = await verified;
		if (!result.ok())
			throw new Error(`Browser MFA failed: ${result.status()}`);
		return (await result.json()).access_token;
	});
}
