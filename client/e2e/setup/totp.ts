/**
 * TOTP Helper for E2E Testing
 *
 * Provides utilities for generating TOTP codes during E2E testing.
 * Port of api/tests/helpers/totp.py to TypeScript.
 *
 * Implements RFC 6238 TOTP (Time-Based One-Time Password) algorithm.
 */

import * as crypto from "crypto";

/**
 * Generate a valid 6-digit TOTP code from a base32 secret.
 *
 * @param secret - Base32-encoded TOTP secret from MFA setup
 * @returns 6-digit TOTP code as string
 */
export function generateTOTP(secret: string, at: number = Date.now()): string {
	const epoch = Math.floor(at / 1000);
	const timeStep = 30;
	const counter = Math.floor(epoch / timeStep);

	// Decode base32 secret
	const key = base32Decode(secret);

	// Create HMAC-SHA1 of counter
	const counterBuffer = Buffer.alloc(8);
	counterBuffer.writeBigUInt64BE(BigInt(counter));

	const hmac = crypto.createHmac("sha1", key);
	hmac.update(counterBuffer);
	const hash = hmac.digest();

	// Dynamic truncation (RFC 4226)
	const offset = hash[hash.length - 1] & 0x0f;
	const code =
		((hash[offset] & 0x7f) << 24) |
		((hash[offset + 1] & 0xff) << 16) |
		((hash[offset + 2] & 0xff) << 8) |
		(hash[offset + 3] & 0xff);

	// Return 6-digit code with leading zeros
	return (code % 1000000).toString().padStart(6, "0");
}

/**
 * Verify a TOTP code against a secret.
 *
 * Checks the current time window and one window before/after
 * to account for clock drift.
 *
 * @param secret - Base32-encoded TOTP secret
 * @param code - 6-digit TOTP code to verify
 * @returns True if valid, false otherwise
 */
export function verifyTOTP(secret: string, code: string): boolean {
	const currentCode = generateTOTP(secret);
	return code === currentCode;
}

/**
 * Decode a base32 string to Buffer.
 *
 * Uses RFC 4648 base32 alphabet (A-Z, 2-7).
 *
 * @param encoded - Base32 encoded string
 * @returns Decoded bytes as Buffer
 */
function base32Decode(encoded: string): Buffer {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

	// Remove padding and convert to uppercase
	const cleanedInput = encoded.replace(/=+$/, "").toUpperCase();

	// Convert each character to 5-bit value
	let bits = "";
	for (const char of cleanedInput) {
		const index = alphabet.indexOf(char);
		if (index === -1) continue; // Skip invalid characters
		bits += index.toString(2).padStart(5, "0");
	}

	// Convert bits to bytes
	const bytes: number[] = [];
	for (let i = 0; i + 8 <= bits.length; i += 8) {
		bytes.push(parseInt(bits.slice(i, i + 8), 2));
	}

	return Buffer.from(bytes);
}
