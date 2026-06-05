/**
 * BifrostHeader — optional platform chrome for a standalone_v2 Solution app.
 *
 * v2 apps own their layout; the platform does NOT impose a shell. This header
 * is a library component an author composes if they want the familiar platform
 * affordances — the app title, a back-to-Bifrost link, and a logout action.
 * Everything it needs (the platform base URL + logout) comes from the
 * `<BifrostProvider>` context, so it works identically in `npm run dev` and
 * when deployed.
 */
import { ArrowLeft, LogOut } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useBifrostContext } from "@/lib/app-sdk/provider";
import { cn } from "@/lib/utils";

export interface BifrostHeaderProps {
  /** App title shown at the left of the header. */
  title: string;
  /** Optional action slot rendered at the right (before logout). */
  action?: ReactNode;
  className?: string;
}

export function BifrostHeader({ title, action, className }: BifrostHeaderProps) {
  const { baseUrl, logout } = useBifrostContext();
  const platformRoot = `${baseUrl.replace(/\/$/, "")}/`;

  return (
    <header
      className={cn(
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
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => logout()}
          aria-label="Log out"
        >
          <LogOut className="mr-1 h-4 w-4" />
          Log out
        </Button>
      </div>
    </header>
  );
}
