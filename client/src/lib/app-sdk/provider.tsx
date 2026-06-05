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
 *   - a React Query client,
 *   - the active org scope,
 *
 * all delivered via React context. The same provider is used identically in
 * `npm run dev` (resolved from node_modules, pointed at a live dev instance via
 * the dev token) and when deployed. The v1 globalThis path is untouched.
 */
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
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
  /** Override `fetch` (tests / non-browser). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Called when the app requests logout (e.g. via `<BifrostHeader>`). */
  onLogout?: () => void;
  /** Provide a shared QueryClient; one is created if omitted. */
  queryClient?: QueryClient;
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
  fetchImpl,
  onLogout,
  queryClient,
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
      authedFetch,
      logout,
    };
  }, [baseUrl, token, orgScope, fetchImpl, onLogout]);

  const client = useMemo(
    () => queryClient ?? new QueryClient(),
    [queryClient],
  );

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
    <BifrostContext.Provider value={value}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </BifrostContext.Provider>
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
