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
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
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
  /**
   * Current theme. Mirrors the platform contract: persisted in
   * localStorage["theme"] and reflected as the `dark` class on the document
   * root, so an app that keys its tokens off `.dark` (or Tailwind's `dark:`)
   * follows the platform — and the platform's own preference — automatically.
   */
  theme: Theme;
  /** Set the theme. Persists + updates the root class; notifies the host. */
  setTheme: (theme: Theme) => void;
  /** Convenience: flip light↔dark. */
  toggleTheme: () => void;
  /**
   * Whether this app declared it SUPPORTS theming (`supportsTheme` prop).
   * BifrostHeader only renders its light/dark toggle when this is true, so an
   * app with hardcoded colors never shows a toggle that would half-break it.
   */
  supportsTheme: boolean;
}

export type Theme = "light" | "dark";

const BifrostContext = createContext<BifrostContextValue | null>(null);

const THEME_KEY = "theme";

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
  /**
   * Declares that this app responds to theme changes (its tokens key off the
   * `dark` class / provided theme). When true, BifrostHeader shows the
   * light/dark toggle. Apps with hardcoded colors omit it (default false) and
   * stay in whatever theme the platform last set, with no toggle. The app owns
   * this because the app renders <BifrostProvider> + <BifrostHeader>.
   */
  supportsTheme?: boolean;
  /**
   * Host-controlled theme. When the platform mounts the app it can pass its
   * current theme so the app starts in sync; omit to let the provider read the
   * shared localStorage["theme"]. `onThemeChange` lets the host persist/echo a
   * change the app makes.
   */
  theme?: Theme;
  onThemeChange?: (theme: Theme) => void;
  children: ReactNode;
}

function readStoredTheme(initial?: Theme): Theme {
  if (initial) return initial;
  if (typeof localStorage !== "undefined") {
    const t = localStorage.getItem(THEME_KEY);
    if (t === "light" || t === "dark") return t;
  }
  return "light";
}

function applyThemeClass(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
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
  supportsTheme = false,
  theme: themeProp,
  onThemeChange,
  children,
}: BifrostProviderProps) {
  // Theme state: seed from the host prop, else the shared localStorage key.
  // Apply the `dark` root class on mount + every change so the app (and the
  // platform) stay visually in sync through one contract.
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme(themeProp));
  useEffect(() => {
    applyThemeClass(theme);
  }, [theme]);
  // Follow the host if it drives the theme prop (platform-controlled mounts).
  // Derive-during-render (the React-sanctioned "previous prop" pattern) — an
  // effect-based sync would cascade an extra render per host theme change.
  const [prevThemeProp, setPrevThemeProp] = useState(themeProp);
  if (themeProp !== prevThemeProp) {
    setPrevThemeProp(themeProp);
    if (themeProp && themeProp !== theme) setThemeState(themeProp);
  }

  const setTheme = useCallback(
    (next: Theme) => {
      setThemeState(next);
      if (typeof localStorage !== "undefined") localStorage.setItem(THEME_KEY, next);
      applyThemeClass(next);
      onThemeChange?.(next);
    },
    [onThemeChange],
  );
  const toggleTheme = useCallback(
    () => setTheme(theme === "dark" ? "light" : "dark"),
    [theme, setTheme],
  );

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
      // Same context signal the tables/files transport sends: the server's
      // auth layer resolves it to the install scope (ctx.solution_id), so a
      // workflow path::fn ref scopes to THIS install without body plumbing.
      if (appId && !headers.has("X-Bifrost-App")) {
        headers.set("X-Bifrost-App", appId);
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
      theme,
      setTheme,
      toggleTheme,
      supportsTheme,
    };
  }, [baseUrl, token, orgScope, appId, fetchImpl, onLogout, theme, setTheme, toggleTheme, supportsTheme]);

  // Route the data SDK (tables.*/useTable) through this provider so a v2 app
  // in `npm run dev` (different origin) reaches the configured Bifrost API with
  // the bearer token + org scope, instead of its own dev server unauthed. v1
  // inline apps never mount a provider and keep the same-origin cookie default.
  //
  // Install SYNCHRONOUSLY during render: child mount effects (e.g. useTable's
  // first snapshot query) run BEFORE this component's own useEffect, so an
  // effect-time install loses the race on first paint — the first query would
  // go out on the default same-origin transport with no token / app header.
  // Render-time assignment is idempotent (same-inputs skip), so StrictMode's
  // double render installs once.
  /* eslint-disable react-hooks/refs -- deliberate render-phase install: the
     transport is a module-global external store that child MOUNT EFFECTS read
     synchronously, so it must be written during render (guarded by a same-key
     skip for idempotence under StrictMode/re-renders). An effect-time install
     is too late by definition here. */
  const installKey = `${baseUrl}|${token}|${orgScope ?? ""}|${appId ?? ""}`;
  const installedRef = useRef<{
    key: string;
    fetchImpl: typeof fetch | undefined;
    restoreTransport: () => void;
    restoreScope: () => void;
  } | null>(null);
  // Restore scheduled by the effect cleanup, pending in a microtask. A
  // re-install (render-time or effect re-run) cancels it by replacing the
  // marker, so StrictMode's synthetic cleanup→re-setup never actually
  // releases the transport while the tree stays mounted.
  const pendingRestoreRef = useRef<object | null>(null);
  const install = () => {
    pendingRestoreRef.current = null;
    const restoreTransport = setBifrostTransport({
      baseUrl: baseUrl.replace(/\/$/, ""),
      // Raw token for the ws client (query-param auth — WebSocket can't send
      // an Authorization header). HTTP calls use the header below.
      token,
      fetchImpl,
      headers: {
        Authorization: `Bearer ${token}`,
        // Identify the calling app so the server resolves a `useTable("name")`
        // call to THIS install's own deployed table, not a sibling install's
        // (the table equivalent of the useWorkflow app_id, Codex #15).
        ...(appId ? { "X-Bifrost-App": appId } : {}),
      },
    });
    const restoreScope = setDefaultAppScope(orgScope);
    const prev = installedRef.current;
    installedRef.current = {
      key: installKey,
      fetchImpl,
      // Keep the FIRST install's restores: unmount must return to the
      // pre-mount transport/scope, not to one of our own intermediates.
      restoreTransport: prev?.restoreTransport ?? restoreTransport,
      restoreScope: prev?.restoreScope ?? restoreScope,
    };
  };
  const installRef = useRef(install);
  installRef.current = install;
  if (
    installedRef.current === null ||
    installedRef.current.key !== installKey ||
    installedRef.current.fetchImpl !== fetchImpl
  ) {
    install();
  }
  /* eslint-enable react-hooks/refs */

  // Unmount cleanup with a DEFERRED release. StrictMode runs ALL passive
  // cleanups (this one included) and then re-runs effects CHILD-FIRST, so a
  // synchronous restore here would expose the default transport to a child's
  // re-run mount effect (e.g. useTable's first query) — the same first-paint
  // bug the render-time install fixes, one effect cycle later. Instead the
  // restore is scheduled in a microtask; the effect re-setup (or any
  // re-install) cancels it before the microtask drains. On a REAL unmount
  // nothing cancels it and the restore lands.
  useEffect(() => {
    // Cancel a restore scheduled by a StrictMode synthetic cleanup — the
    // tree is still mounted and the transport must stay installed.
    pendingRestoreRef.current = null;
    if (installedRef.current === null) installRef.current();
    return () => {
      const installed = installedRef.current;
      if (installed === null) return;
      const pending = {};
      pendingRestoreRef.current = pending;
      queueMicrotask(() => {
        if (pendingRestoreRef.current !== pending) return;
        pendingRestoreRef.current = null;
        installedRef.current = null;
        installed.restoreTransport();
        installed.restoreScope();
      });
    };
  }, []);

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
