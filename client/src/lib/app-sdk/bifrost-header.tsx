/**
 * BifrostHeader — platform chrome for a standalone_v2 app, shipped in the
 * installable `bifrost` SDK. Mirrors the platform's own top header as closely
 * as a self-contained component can: optional app LOGO + title on the left, an
 * optional action slot, and a USER MENU on the right (avatar/initials + name →
 * dropdown with name/email, Back to Bifrost, Log out).
 *
 * v2 apps own their layout; the platform imposes no shell. This header is a
 * LIBRARY component an author composes if they want the familiar affordances.
 *
 * SELF-CONTAINED by necessity: the in-client header uses shadcn DropdownMenu /
 * Avatar / Button via `@/` aliases that don't resolve outside the client
 * project (and would drag shadcn + Tailwind into every v2 bundle). So this copy
 * rebuilds the SAME UX with inline styles + a tiny scoped <style> for hover and
 * the dropdown. It does NOT depend on Tailwind or the platform CSS-variable
 * theme — drop-in correct in `npm run dev`, deployed, or any standalone bundle.
 *
 * User identity + app logo are fetched lazily from the authed context the
 * provider already supplies (`authedFetch` + `appId`) — no new bootstrap
 * fields, no provider change. `GET /api/auth/me` → name/email/avatar;
 * `GET /api/applications/{appId}` → logo data URL. Both degrade gracefully
 * (initials fallback, no logo) if unavailable.
 */
import { ArrowLeft, ChevronDown, LogOut, Moon, Sun } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { useBifrostContext } from "./provider";

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

// The hover selectors are qualified by the theme attribute (``[data-bifrost-header-theme="..."]``),
// NOT a bare ``[data-bifrost-header]`` — otherwise a light and a dark header on
// the same page would share one global selector and whichever stylesheet was
// appended last would set hover colors for BOTH (Codex). Theme-qualified +
// theme-suffixed id means each theme's sheet only styles its own headers.
function scopedCss(C: Palette, themeKey: string): string {
  const s = `[data-bifrost-header][data-bifrost-header-theme="${themeKey}"]`;
  return `
${s} .bfh-link,${s} .bfh-trigger{color:${C.muted};transition:color .12s,background-color .12s}
${s} .bfh-link:hover{color:${C.fg}}
${s} .bfh-trigger:hover{color:${C.fg};background-color:${C.accent}}
${s} .bfh-item:hover{background-color:${C.accent}}
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

function initials(me: Me | null): string {
  const src = me?.name || me?.email || "";
  if (!src) return "?";
  const parts = src.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src[0].toUpperCase();
}

// Palette-independent layout (shared by light + dark).
const leftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.7rem",
  minWidth: 0,
  flex: "1 1 240px",
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

// Palette-keyed chrome (the parts that recolor between light + dark).
const headerStyle = (C: Palette): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  flexWrap: "wrap",
  gap: "0.5rem 1rem",
  borderBottom: `1px solid ${C.border}`,
  padding: "0.5rem 1rem",
  background: C.surface,
  fontFamily: "ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
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

export function BifrostHeader({ title, logo, action, className }: BifrostHeaderProps) {
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

  const [me, setMe] = useState<Me | null>(null);
  const [fetchedLogo, setFetchedLogo] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Fetch the signed-in user once.
  useEffect(() => {
    let cancelled = false;
    authedFetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => !cancelled && d && setMe(d))
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

  // Close the menu on outside click / Escape.
  useEffect(() => {
    if (!open) return;
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
  }, [open]);

  const effectiveLogo = logo !== undefined ? logo : fetchedLogo;
  const name = me?.name || me?.email?.split("@")[0] || "Account";
  const email = me?.email || "";

  return (
    <header data-bifrost-header data-bifrost-header-theme={themeKey} style={headerStyle(C)} className={className}>
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
            onClick={() => setOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label="Account menu"
            style={triggerStyle}
          >
            <span style={avatarStyle(C, 26)}>
              {me?.avatar_url ? (
                <img src={me.avatar_url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              ) : (
                initials(me)
              )}
            </span>
            <span style={accountNameStyle(C)}>{name}</span>
            <ChevronDown style={{ ...iconStyle, color: C.faint }} />
          </button>

          {open && (
            <div role="menu" style={menuStyle(C)}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem", padding: "0.5rem 0.625rem" }}>
                <span style={avatarStyle(C, 36)}>
                  {me?.avatar_url ? (
                    <img src={me.avatar_url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : (
                    initials(me)
                  )}
                </span>
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
              <div style={{ height: 1, background: C.border, margin: "4px 0" }} />
              <a href={platformApps} className="bfh-item" role="menuitem" style={{ ...menuItemStyle(C), textDecoration: "none" }}>
                <ArrowLeft style={iconStyle} />
                Back to Bifrost
              </a>
              <button
                type="button"
                className="bfh-item"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  logout();
                }}
                style={{ ...menuItemStyle(C), color: C.danger }}
              >
                <LogOut style={iconStyle} />
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
