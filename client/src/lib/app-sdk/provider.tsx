/**
 * BifrostProvider — the v2 standalone-app SDK root (criterion 12).
 *
 * A `standalone_v2` Solution app is a normal React project: it owns its
 * `createRoot` and its `<BrowserRouter>`, and imports the SDK as a real package.
 * Instead of reaching for `globalThis.__bifrost_platform` (the v1 inline path),
 * a v2 app wraps its tree in `<BifrostProvider baseUrl token orgScope>`, which
 * establishes:
 *
 *   - an authed `fetch` (bearer token + base-url join),
 *   - the active org scope,
 *
 * all delivered via React context. The SDK deliberately depends on NOTHING but
 * `react` itself (a peer dep — hooks need it): data hooks (`useTable`,
 * `useWorkflow`) use plain `fetch` + `useState`, not a data-fetching library.
 * The same provider is used identically in `npm run dev` (resolved from
 * node_modules, pointed at a live dev instance via the dev token) and when
 * deployed. The v1 globalThis path is untouched.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";

import { setBifrostTransport, setDefaultAppScope } from "./tables";

export interface BifrostContextValue {
  /** Absolute base URL of the Bifrost API (no trailing slash). */
  baseUrl: string;
  /** Bearer access token for API calls. */
  token: string;
  /** Active organization scope (UUID), or null for the caller's default. */
  orgScope: string | null;
  /**
   * This app's id, when mounted as a Solution app. `useWorkflow` sends it on
   * execute so a `path::function` ref resolves to THIS install's own workflow,
   * not a sibling install's that shares the path (Codex #8 P1). null in dev /
   * when the host doesn't supply it.
   */
  appId: string | null;
  /** `fetch` that joins `baseUrl` and attaches the bearer token. */
  authedFetch: typeof fetch;
  /** Log the user out. No-op if the app did not supply `onLogout`. */
  logout: () => void;
}

const BifrostContext = createContext<BifrostContextValue | null>(null);

export interface BifrostProviderProps {
  baseUrl: string;
  token: string;
  orgScope?: string | null;
  /** This app's id (forwarded to execute so path refs resolve to this install). */
  appId?: string | null;
  /** Override `fetch` (tests / non-browser). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Called when the app requests logout (e.g. a platform "log out" action). */
  onLogout?: () => void;
  children: ReactNode;
}

function joinUrl(baseUrl: string, input: RequestInfo | URL): RequestInfo | URL {
  // Only rewrite string, root-relative API paths; leave absolute URLs and
  // Request objects untouched.
  if (typeof input !== "string") return input;
  if (/^https?:\/\//i.test(input)) return input;
  const base = baseUrl.replace(/\/$/, "");
  const path = input.startsWith("/") ? input : `/${input}`;
  return `${base}${path}`;
}

export function BifrostProvider({
  baseUrl,
  token,
  orgScope = null,
  appId = null,
  fetchImpl,
  onLogout,
  children,
}: BifrostProviderProps) {
  const value = useMemo<BifrostContextValue>(() => {
    const baseFetch = fetchImpl ?? globalThis.fetch;
    const authedFetch: typeof fetch = (input, init) => {
      const headers = new Headers(init?.headers);
      if (!headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      if (orgScope && !headers.has("X-Bifrost-Org")) {
        headers.set("X-Bifrost-Org", orgScope);
      }
      return baseFetch(joinUrl(baseUrl, input), { ...init, headers });
    };
    const logout = () => onLogout?.();
    return {
      baseUrl: baseUrl.replace(/\/$/, ""),
      token,
      orgScope,
      appId,
      authedFetch,
      logout,
    };
  }, [baseUrl, token, orgScope, appId, fetchImpl, onLogout]);

  // Route the data SDK (tables.*/useTable) through this provider so a v2 app
  // in `npm run dev` (different origin) reaches the configured Bifrost API with
  // the bearer token + org scope, instead of its own dev server unauthed. v1
  // inline apps never mount a provider and keep the same-origin cookie default.
  useEffect(() => {
    const restore = setBifrostTransport({
      baseUrl: baseUrl.replace(/\/$/, ""),
      fetchImpl,
      headers: { Authorization: `Bearer ${token}` },
    });
    const restoreScope = setDefaultAppScope(orgScope);
    return () => {
      restore();
      restoreScope();
    };
  }, [baseUrl, token, orgScope, fetchImpl]);

  return (
    <BifrostContext.Provider value={value}>{children}</BifrostContext.Provider>
  );
}

/**
 * Read the Bifrost SDK context. Throws if called outside a `<BifrostProvider>`
 * — a v2 app must wrap its root in the provider; the v1 inline path uses the
 * globalThis proxy instead and never calls this.
 */
export function useBifrostContext(): BifrostContextValue {
  const ctx = useContext(BifrostContext);
  if (ctx === null) {
    throw new Error(
      "useBifrostContext must be used within a <BifrostProvider>. " +
        "A standalone_v2 app must wrap its root in <BifrostProvider baseUrl token>.",
    );
  }
  return ctx;
}
