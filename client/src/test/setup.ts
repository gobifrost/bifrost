import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";

// happy-dom >=20.12 ships a partial Web Animations API (Element.prototype.animate).
// framer-motion detects this and drives `motion.*` components through the native
// WAAPI path instead of its own JS animation fallback. happy-dom's Animation.cancel()
// rejects the spec's `finished` promise with an AbortError, and nothing in that path
// attaches a handler to it, so unmounting a component mid-animation (e.g. a dialog or
// route transition closing during a test) throws an unhandled rejection and fails the
// run. Real browsers have the same spec behavior, but only Vitest's Node process turns
// an unhandled rejection into a hard failure. Force framer-motion back onto its JS
// animation path in tests, matching how it already ran before happy-dom added this API
// and how it still behaves in any environment without WAAPI support.
delete (Element.prototype as { animate?: unknown }).animate;

// Start every test from a clean DOM even if a previous file timed out before
// its own afterEach cleanup could run.
beforeEach(() => {
	cleanup();
});

// Unmount after every test so DOM state doesn't leak between cases.
afterEach(() => {
	cleanup();
});
