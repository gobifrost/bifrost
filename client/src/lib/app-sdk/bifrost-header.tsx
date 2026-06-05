/**
 * BifrostHeader — optional platform chrome for a standalone_v2 app, shipped in
 * the installable `bifrost` SDK.
 *
 * v2 apps own their layout; the platform imposes no shell (it renders v2 apps
 * full-page). This header is a LIBRARY component an author composes if they want
 * the familiar affordances — app title, back-to-Bifrost link, logout. Base URL +
 * logout come from `<BifrostProvider>` context, so it works identically in
 * `npm run dev` and deployed.
 *
 * This is a SELF-CONTAINED copy (no `@/components/ui/button`, no `@/lib/utils`):
 * the in-client BifrostHeader pulls shadcn `Button` + `cn` via `@/` aliases that
 * don't resolve outside the client project, so shipping that one would drag
 * shadcn into every v2 app bundle. A plain button + inline class join keeps the
 * SDK's only new dep `lucide-react` (a peer the app already has). Codex R4.
 */
import { ArrowLeft, LogOut } from "lucide-react";
import type { ReactNode } from "react";

import { useBifrostContext } from "./provider";

export interface BifrostHeaderProps {
  /** App title shown at the left of the header. */
  title: string;
  /** Optional action slot rendered at the right (before logout). */
  action?: ReactNode;
  className?: string;
}

function join(...parts: Array<string | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function BifrostHeader({ title, action, className }: BifrostHeaderProps) {
  const { baseUrl, logout } = useBifrostContext();
  const platformRoot = `${baseUrl.replace(/\/$/, "")}/`;

  return (
    <header
      className={join(
        "flex items-center justify-between gap-4 border-b px-4 py-2",
        className,
      )}
    >
      <div className="flex items-center gap-3">
        <a
          href={platformRoot}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Bifrost
        </a>
        <span className="text-base font-semibold">{title}</span>
      </div>
      <div className="flex items-center gap-2">
        {action}
        <button
          type="button"
          onClick={() => logout()}
          aria-label="Log out"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <LogOut className="h-4 w-4" />
          Log out
        </button>
      </div>
    </header>
  );
}
