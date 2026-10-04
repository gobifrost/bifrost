/**
 * BifrostHeader — platform chrome for a standalone_v2 app, shipped in the
 * installable `bifrost` SDK. Mirrors the platform's own top header as closely
 * as a self-contained component can: optional app LOGO + title on the left, an
 * optional action slot, and a USER MENU on the right (avatar/initials + name →
 * dropdown with name/email, Back to Bifrost, Log out). An optional `nav` adds
 * the app's section links as a tab row under the title bar.
 *
 * v2 apps own their layout; the platform imposes no shell. This header is a
 * LIBRARY component an author composes if they want the familiar affordances.
 *
 * SELF-CONTAINED by necessity: the in-client header uses shadcn DropdownMenu /
 * Avatar / Button via `@/` aliases that don't resolve outside the client
 * project (and would drag shadcn + Tailwind into every v2 bundle). So this copy
 * rebuilds the SAME UX with inline styles + a tiny scoped <style> for hover and
 * active states. It does NOT depend on Tailwind or the platform CSS-variable
 * theme — drop-in correct in `npm run dev`, deployed, or any standalone bundle.
 * It does not depend on a router either: the app passes its router's NavLink.
 *
 * PHONES (below 640px): one row — a menu button on the left, then the truncated
 * title (the navigation-drawer layout). The button opens a full-width panel
 * under the bar holding Back to Bifrost, the nav links, the action slot, the
 * theme toggle and the account section. Focus moves into the panel and Tab is
 * kept inside it; Escape, a tap outside, following a link, or browser
 * back/forward closes it.
 *
 * User identity + app logo are fetched lazily from the authed context the
 * provider already supplies (`authedFetch` + `appId`) — no new bootstrap
 * fields, no provider change. `GET /api/auth/me` → name/email/avatar;
 * `GET /api/applications/{appId}` → logo data URL. Both degrade gracefully
 * (initials fallback, no logo) if unavailable.
 */
import { ArrowLeft, ChevronDown, LogOut, Menu, Moon, Sun, X } from "lucide-react";
import type { ComponentType, CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";

import { useBifrostContext } from "./provider";

/** One section link. `to` is relative to the app's router basename. */
export interface BifrostNavItem {
  label: string;
  to: string;
  /** Match `to` exactly (e.g. the home route), as react-router's NavLink `end`. */
  end?: boolean;
}

/**
 * Props the header passes to `nav.link`. react-router's `NavLink` satisfies
 * this directly; any other link component must set `aria-current="page"` on
 * the active link, which is what the header styles as active.
 */
export interface BifrostNavLinkProps {
  to: string;
  end?: boolean;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
  children: ReactNode;
}

export interface BifrostHeaderNav {
  items: BifrostNavItem[];
  /** The router's link component, e.g. `NavLink` from react-router-dom. */
  link: ComponentType<BifrostNavLinkProps>;
}

export interface BifrostHeaderProps {
  /** App title shown next to the logo on the left. */
  title: string;
  /**
   * App logo. Pass a URL/data-URL to control it explicitly; omit to let the
   * header fetch the deployed app's logo via `appId`. Pass `null` to force no
   * logo even if the app has one.
   */
  logo?: string | null;
  /** Optional action slot rendered at the right (before the user menu). */
  action?: ReactNode;
  /** Optional section links: a tab row on wide screens, the menu panel on phones. */
  nav?: BifrostHeaderNav;
  className?: string;
}

// Self-contained palette. A theme-aware app (supportsTheme) flips the header's
// OWN chrome to dark when the theme is dark, so it doesn't sit as a light bar
// above a dark app (the D3 half-themed-header miss). An app that doesn't support
// theming always gets the light palette — its colors are hardcoded light too.
interface Palette {
  border: string;
  fg: string;
  muted: string;
  faint: string;
  accent: string;
  surface: string;
  danger: string;
  brand: string;
}

const LIGHT: Palette = {
  border: "#e4e4e7",
  fg: "#18181b",
  muted: "#71717a",
  faint: "#a1a1aa",
  accent: "#f4f4f5",
  surface: "#ffffff",
  danger: "#dc2626",
  brand: "#2563eb",
};

const DARK: Palette = {
  border: "#27272a",
  fg: "#fafafa",
  muted: "#a1a1aa",
  faint: "#71717a",
  accent: "#27272a",
  surface: "#18181b",
  danger: "#f87171",
  brand: "#3b82f6",
};

interface Me {
  name?: string;
  email?: string;
  avatar_url?: string;
}

const STYLE_ID = "bifrost-header-style";

/** Below this width the header collapses to the single menu button. */
const BIFROST_HEADER_NARROW_QUERY = "(max-width: 639.98px)";

// The hover selectors are qualified by the theme attribute (``[data-bifrost-header-theme="..."]``),
// NOT a bare ``[data-bifrost-header]`` — otherwise a light and a dark header on
// the same page would share one global selector and whichever stylesheet was
// appended last would set hover colors for BOTH (Codex). Theme-qualified +
// theme-suffixed id means each theme's sheet only styles its own headers.
// Active nav links are marked by the router with aria-current="page".
function scopedCss(C: Palette, themeKey: string): string {
  const s = `[data-bifrost-header][data-bifrost-header-theme="${themeKey}"]`;
  return `
${s} .bfh-link,${s} .bfh-trigger,${s} .bfh-tab{color:${C.muted};transition:color .12s,background-color .12s}
${s} .bfh-link:hover{color:${C.fg}}
${s} .bfh-trigger:hover{color:${C.fg};background-color:${C.accent}}
${s} .bfh-item:hover{background-color:${C.accent}}
${s} .bfh-tab{border-bottom-color:transparent}
${s} .bfh-tab:hover{color:${C.fg}}
${s} .bfh-tab[aria-current="page"]{color:${C.fg};border-bottom-color:${C.fg}}
${s} .bfh-panel-link[aria-current="page"]{background-color:${C.accent};font-weight:600}
`;
}

function ensureStyle(C: Palette, themeKey: string): void {
  if (typeof document === "undefined") return;
  const id = `${STYLE_ID}-${themeKey}`;
  if (document.getElementById(id)) return;
  const el = document.createElement("style");
  el.id = id;
  el.textContent = scopedCss(C, themeKey);
  document.head.appendChild(el);
}

function subscribeNarrow(onChange: () => void): () => void {
  const query = window.matchMedia(BIFROST_HEADER_NARROW_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isNarrow(): boolean {
  return window.matchMedia(BIFROST_HEADER_NARROW_QUERY).matches;
}

function initials(me: Me | null): string {
  const src = me?.name || me?.email || "";
  if (!src) return "?";
  const parts = src.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src[0].toUpperCase();
}

// Palette-independent layout (shared by light + dark). The left side has a zero
// flex basis so it only receives the space the right cluster leaves over: the
// title truncates before the theme toggle or user menu is squeezed or wrapped.
const barStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  flexWrap: "nowrap",
  gap: "1rem",
  padding: "0.5rem 1rem",
};
const leftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.7rem",
  minWidth: 0,
  flex: "1 1 0%",
  overflow: "hidden",
};
const logoStyle: CSSProperties = {
  height: 26,
  width: "auto",
  borderRadius: 5,
  display: "block",
  flexShrink: 0,
};
const rightStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: "0.5rem",
  flex: "0 1 auto",
  flexWrap: "wrap",
  minWidth: 0,
  maxWidth: "100%",
};
const iconStyle: CSSProperties = { width: "1rem", height: "1rem" };
const triggerStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.5rem",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  borderRadius: "0.5rem",
  padding: "0.25rem 0.5rem",
  fontSize: "0.875rem",
  fontFamily: "inherit",
  minWidth: 0,
  maxWidth: "100%",
};
const tabRowStyle: CSSProperties = {
  display: "flex",
  gap: "0.25rem",
  overflowX: "auto",
  whiteSpace: "nowrap",
  padding: "0 1rem",
};
const tabStyle: CSSProperties = {
  padding: "0.5rem 0.75rem",
  fontSize: "0.875rem",
  textDecoration: "none",
  borderBottomWidth: 2,
  borderBottomStyle: "solid",
};
const panelSectionStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: 6,
};

// Palette-keyed chrome (the parts that recolor between light + dark).
const headerStyle = (C: Palette): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  borderBottom: `1px solid ${C.border}`,
  background: C.surface,
  fontFamily:
    "var(--bf-font-sans, Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif)",
  position: "relative",
});
const backLinkStyle = (C: Palette): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.25rem",
  fontSize: "0.8125rem",
  textDecoration: "none",
  paddingRight: "0.7rem",
  borderRight: `1px solid ${C.border}`,
  flexShrink: 0,
});
const titleStyle = (C: Palette): CSSProperties => ({
  fontSize: "0.95rem",
  fontWeight: 600,
  color: C.fg,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  minWidth: 0,
});
const avatarStyle = (C: Palette, size: number): CSSProperties => ({
  width: size,
  height: size,
  borderRadius: "9999px",
  background: C.accent,
  color: C.fg,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: size < 32 ? "0.7rem" : "0.85rem",
  fontWeight: 600,
  flexShrink: 0,
  overflow: "hidden",
});
const menuStyle = (C: Palette): CSSProperties => ({
  position: "absolute",
  top: "calc(100% - 2px)",
  right: 0,
  width: 232,
  maxWidth: "calc(100vw - 2rem)",
  background: C.surface,
  border: `1px solid ${C.border}`,
  borderRadius: "0.625rem",
  boxShadow: "0 12px 32px rgba(24,24,27,0.14)",
  padding: 6,
  zIndex: 70,
});
const menuItemStyle = (C: Palette): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  width: "100%",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  borderRadius: "0.375rem",
  padding: "0.5rem 0.625rem",
  fontSize: "0.875rem",
  fontFamily: "inherit",
  color: C.fg,
  textAlign: "left",
  textDecoration: "none",
});
const dividerStyle = (C: Palette): CSSProperties => ({ height: 1, background: C.border, margin: "4px 0" });
// The panel hangs from the header's bottom edge across the full width; the
// backdrop behind it dims the page and closes the panel when tapped.
const backdropStyle: CSSProperties = {
  position: "absolute",
  top: "100%",
  left: 0,
  right: 0,
  height: "100vh",
  background: "rgba(24,24,27,0.4)",
  zIndex: 60,
};
const panelStyle = (C: Palette): CSSProperties => ({
  position: "absolute",
  top: "100%",
  left: 0,
  right: 0,
  maxHeight: "calc(100vh - 4rem)",
  overflowY: "auto",
  background: C.surface,
  borderBottom: `1px solid ${C.border}`,
  boxShadow: "0 12px 32px rgba(24,24,27,0.14)",
  zIndex: 70,
});

const accountNameStyle = (C: Palette): CSSProperties => ({
  color: C.fg,
  fontWeight: 500,
  maxWidth: "min(10rem, 35vw)",
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});

function Avatar({ me, C, size }: { me: Me | null; C: Palette; size: number }) {
  return (
    <span style={avatarStyle(C, size)}>
      {me?.avatar_url ? (
        <img src={me.avatar_url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        initials(me)
      )}
    </span>
  );
}

function AccountSummary({ me, C, name, email }: { me: Me | null; C: Palette; name: string; email: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", padding: "0.5rem 0.625rem" }}>
      <Avatar me={me} C={C} size={36} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: "0.875rem", fontWeight: 600, color: C.fg, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {name}
        </div>
        {email ? (
          <div style={{ fontSize: "0.75rem", color: C.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {email}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function NavItems({
  nav,
  className,
  style,
  onClick,
}: {
  nav: BifrostHeaderNav;
  className: string;
  style: CSSProperties;
  onClick?: () => void;
}) {
  const Link = nav.link;
  return (
    <>
      {nav.items.map((item) => (
        <Link key={item.to} to={item.to} end={item.end} className={className} style={style} onClick={onClick}>
          {item.label}
        </Link>
      ))}
    </>
  );
}

export function BifrostHeader({ title, logo, action, nav, className }: BifrostHeaderProps) {
  const { baseUrl, appId, authedFetch, logout, theme, toggleTheme, supportsTheme } =
    useBifrostContext();
  // Back link target: the platform's Apps page (where the user came from),
  // not the bare root — "← Bifrost" / "Back to Bifrost" return to /apps.
  const platformApps = `${baseUrl.replace(/\/$/, "")}/apps`;
  // Only a theme-aware app recolors the chrome; otherwise stay light (the app's
  // own colors are hardcoded light, so a dark header would clash).
  const dark = supportsTheme && theme === "dark";
  const C = dark ? DARK : LIGHT;
  const themeKey = dark ? "dark" : "light";
  ensureStyle(C, themeKey);

  // The user menu belongs to the layout it was opened in.
  const [open, setOpen] = useState(false);
  // Close the old layout's menu in the viewport subscription. Resetting state
  // during rendering can discard the external-store bookkeeping needed for
  // the next viewport change in production React.
  const subscribeLayout = useCallback((onChange: () => void) => subscribeNarrow(() => {
    setOpen(false);
    onChange();
  }), []);
  const narrow = useSyncExternalStore(subscribeLayout, isNarrow);

  const [me, setMe] = useState<Me | null>(null);
  const [authExpired, setAuthExpired] = useState(false);
  const [fetchedLogo, setFetchedLogo] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const panelTriggerRef = useRef<HTMLButtonElement | null>(null);
  const panelId = useId();

  // Fetch the signed-in user once.
  useEffect(() => {
    let cancelled = false;
    authedFetch("/api/auth/me")
      .then((r) => {
        if (r.ok) return r.json();
        if (r.status === 401 && r.headers.get("X-Bifrost-Dev-Auth") === "expired") {
          setAuthExpired(true);
        }
        return null;
      })
      .then((d) => {
        if (cancelled || !d) return;
        setAuthExpired(false);
        setMe(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [authedFetch]);

  // Fetch the deployed app's logo when not explicitly provided. Use the
  // dedicated /logo image endpoint (readable by anyone who can mount the app,
  // incl. external/portal users) rather than the role-gated metadata endpoint
  // that 404s for them. Authed fetch → blob → object URL (an <img src> can't
  // carry the bearer header itself).
  useEffect(() => {
    if (logo !== undefined || !appId) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    authedFetch(`/api/applications/${appId}/logo`)
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (cancelled || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setFetchedLogo(objectUrl);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [authedFetch, appId, logo]);

  // Wide user menu: close on outside click / Escape.
  useEffect(() => {
    if (!open || narrow) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, narrow]);

  // Phone panel: focus its first control, keep Tab inside it, close on Escape
  // (returning focus to the menu button) and on browser back/forward.
  useEffect(() => {
    if (!open || !narrow) return;
    const panel = panelRef.current!;
    const focusables = () =>
      Array.from(panel.querySelectorAll<HTMLElement>("a[href], button:not([disabled])"));
    focusables()[0].focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        panelTriggerRef.current?.focus();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    const onPop = () => setOpen(false);
    document.addEventListener("keydown", onKey);
    window.addEventListener("popstate", onPop);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("popstate", onPop);
    };
  }, [open, narrow]);

  const effectiveLogo = logo !== undefined ? logo : fetchedLogo;
  const name = authExpired ? "Session expired" : me?.name || me?.email?.split("@")[0] || "Account";
  const email = me?.email || "";
  const closeMenu = () => setOpen(false);
  const onLogout = () => {
    setOpen(false);
    logout();
  };

  return (
    <header data-bifrost-header data-bifrost-header-theme={themeKey} style={headerStyle(C)} className={className}>
      {narrow ? (
        <div style={{ ...barStyle, justifyContent: "flex-start", gap: "0.5rem" }}>
          <button
            ref={panelTriggerRef}
            type="button"
            className="bfh-trigger"
            onClick={() => setOpen(!open)}
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            aria-controls={panelId}
            style={{ ...triggerStyle, padding: "0.4rem", flexShrink: 0 }}
          >
            {open ? <X style={iconStyle} /> : <Menu style={iconStyle} />}
          </button>
          <span style={titleStyle(C)}>{title}</span>
        </div>
      ) : (
        <div style={barStyle}>
          <div style={leftStyle}>
            <a href={platformApps} className="bfh-link" style={backLinkStyle(C)}>
              <ArrowLeft style={iconStyle} />
              Bifrost
            </a>
            {effectiveLogo ? <img src={effectiveLogo} alt="" style={logoStyle} /> : null}
            <span style={titleStyle(C)}>{title}</span>
          </div>

          <div style={rightStyle}>
            {action}
            {/* Light/dark toggle — only when the app declared it supports theming
                (supportsTheme on BifrostProvider). Apps with hardcoded colors omit
                it and no toggle shows. */}
            {supportsTheme && (
              <button
                type="button"
                className="bfh-trigger"
                onClick={toggleTheme}
                aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
                title={theme === "dark" ? "Light mode" : "Dark mode"}
                style={{ ...triggerStyle, padding: "0.4rem" }}
              >
                {theme === "dark" ? <Sun style={iconStyle} /> : <Moon style={iconStyle} />}
              </button>
            )}
            <div ref={menuRef} style={{ position: "relative" }}>
              <button
                type="button"
                className="bfh-trigger"
                onClick={() => setOpen(!open)}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-label="Account menu"
                style={triggerStyle}
              >
                <Avatar me={me} C={C} size={26} />
                <span style={accountNameStyle(C)}>{name}</span>
                <ChevronDown style={{ ...iconStyle, color: C.faint }} />
              </button>

              {open && (
                <div role="menu" style={menuStyle(C)}>
                  <AccountSummary me={me} C={C} name={name} email={email} />
                  <div style={dividerStyle(C)} />
                  <a href={platformApps} className="bfh-item" role="menuitem" style={menuItemStyle(C)}>
                    <ArrowLeft style={iconStyle} />
                    Back to Bifrost
                  </a>
                  <button
                    type="button"
                    className="bfh-item"
                    role="menuitem"
                    onClick={onLogout}
                    style={{ ...menuItemStyle(C), color: C.danger }}
                  >
                    <LogOut style={iconStyle} />
                    Log out
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {nav && !narrow && (
        <nav aria-label="Primary" style={tabRowStyle}>
          <NavItems nav={nav} className="bfh-tab" style={tabStyle} />
        </nav>
      )}

      {narrow && open && (
        <>
          <div aria-hidden="true" className="bfh-backdrop" style={backdropStyle} onClick={closeMenu} />
          <div ref={panelRef} id={panelId} role="dialog" aria-label="Menu" style={panelStyle(C)}>
            <div style={panelSectionStyle}>
              <a href={platformApps} className="bfh-item" style={menuItemStyle(C)}>
                <ArrowLeft style={iconStyle} />
                Back to Bifrost
              </a>
            </div>
            {nav && (
              <>
                <div style={dividerStyle(C)} />
                <nav aria-label="Primary" style={panelSectionStyle}>
                  <NavItems nav={nav} className="bfh-item bfh-panel-link" style={menuItemStyle(C)} onClick={closeMenu} />
                </nav>
              </>
            )}
            {(action || supportsTheme) && (
              <>
                <div style={dividerStyle(C)} />
                <div style={panelSectionStyle}>
                  {action}
                  {supportsTheme && (
                    <button type="button" className="bfh-item" onClick={toggleTheme} style={menuItemStyle(C)}>
                      {theme === "dark" ? <Sun style={iconStyle} /> : <Moon style={iconStyle} />}
                      {theme === "dark" ? "Light mode" : "Dark mode"}
                    </button>
                  )}
                </div>
              </>
            )}
            <div style={dividerStyle(C)} />
            <div style={panelSectionStyle}>
              <AccountSummary me={me} C={C} name={name} email={email} />
              <button type="button" className="bfh-item" onClick={onLogout} style={{ ...menuItemStyle(C), color: C.danger }}>
                <LogOut style={iconStyle} />
                Log out
              </button>
            </div>
          </div>
        </>
      )}
    </header>
  );
}
