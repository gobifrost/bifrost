/**
 * Embed Settings Dialog
 *
 * Manages embed secrets and shows integration guide for iframe embedding.
 * Opens from the app code editor header.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Plus,
  Trash2,
  Copy,
  Check,
  AlertTriangle,
  Link,
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { authFetch } from "@/lib/api-client";
import { copyToClipboard } from "@/lib/clipboard";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";

// ============================================================================
// Types
// ============================================================================

type HmacScheme = "shopify" | "halopsa";

interface EmbedSecret {
  id: string;
  name: string;
  is_active: boolean;
  hmac_scheme: HmacScheme;
  created_at: string;
}

interface EmbedSecretCreated extends EmbedSecret {
  raw_secret: string;
}

interface Props {
  appId: string;
  appSlug: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// ============================================================================
// Component
// ============================================================================

export function EmbedSettingsDialog({
  appId,
  appSlug,
  open,
  onOpenChange,
}: Props) {
  // Create dialog state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSecret, setCreateSecret] = useState("");
  const [createScheme, setCreateScheme] = useState<HmacScheme>("shopify");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const createInFlightRef = useRef(false);

  // Reveal dialog state (shown once after creation)
  const [revealedSecret, setRevealedSecret] = useState<EmbedSecretCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const copyResetTimeoutRef = useRef<number | null>(null);
  const copyOperationRef = useRef(0);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<EmbedSecret | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const deleteInFlightRef = useRef(false);
  const [updatingSecretId, setUpdatingSecretId] = useState<string | null>(null);
  const updatingSecretIdsRef = useRef(new Set<string>());

  const {
    data: secrets = [],
    isLoading: isSecretsLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["applications", appId, "embed-secrets"],
    enabled: open,
    queryFn: async () => {
      const res = await authFetch(`/api/applications/${appId}/embed-secrets`);
      if (!res.ok) {
        throw new Error(await res.text());
      }
      return (await res.json()) as EmbedSecret[];
    },
  });

  const clearCopyTimer = useCallback(() => {
    if (copyResetTimeoutRef.current !== null) {
      window.clearTimeout(copyResetTimeoutRef.current);
      copyResetTimeoutRef.current = null;
    }
  }, []);

  const invalidateCopyOperation = useCallback(() => {
    copyOperationRef.current += 1;
    clearCopyTimer();
  }, [clearCopyTimer]);

  useEffect(() => {
    return invalidateCopyOperation;
  }, [invalidateCopyOperation]);

  // ========================================================================
  // Actions
  // ========================================================================

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createName.trim() || createInFlightRef.current) return;

    createInFlightRef.current = true;
    setCreateError(null);
    setIsCreating(true);
    try {
      const res = await authFetch(
        `/api/applications/${appId}/embed-secrets`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: createName.trim(),
            hmac_scheme: createScheme,
            ...(createSecret.trim() && { secret: createSecret.trim() }),
          }),
        },
      );
      if (!res.ok) throw new Error(await res.text());
      const created: EmbedSecretCreated = await res.json();
      setRevealedSecret(created);
      setIsCreateOpen(false);
      setCreateName("");
      setCreateSecret("");
      setCreateScheme("shopify");
      void refetch();
      toast.success("Embed secret created");
    } catch {
      setCreateError("Failed to create embed secret. Your values are preserved; try again.");
      toast.error("Failed to create embed secret");
    } finally {
      setIsCreating(false);
      createInFlightRef.current = false;
    }
  };

  const handleToggleActive = async (secret: EmbedSecret) => {
    if (
      updatingSecretIdsRef.current.has(secret.id) ||
      deleteInFlightRef.current ||
      createInFlightRef.current
    ) {
      return;
    }
    updatingSecretIdsRef.current.add(secret.id);
    setUpdatingSecretId(secret.id);
    try {
      const res = await authFetch(
        `/api/applications/${appId}/embed-secrets/${secret.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: !secret.is_active }),
        },
      );
      if (!res.ok) throw new Error(await res.text());
      void refetch();
      toast.success(
        secret.is_active ? "Secret deactivated" : "Secret activated",
      );
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to update secret",
      );
    } finally {
      updatingSecretIdsRef.current.delete(secret.id);
      setUpdatingSecretId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleteInFlightRef.current) return;
    deleteInFlightRef.current = true;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      const res = await authFetch(
        `/api/applications/${appId}/embed-secrets/${deleteTarget.id}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(await res.text());
      setDeleteTarget(null);
      setDeleteError(null);
      void refetch();
      toast.success("Secret deleted");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete secret");
      toast.error("Failed to delete secret");
    } finally {
      setIsDeleting(false);
      deleteInFlightRef.current = false;
    }
  };

  const handleCopy = async (
    text: string,
    successMessage: string,
    failureMessage: string,
  ) => {
    const operationId = ++copyOperationRef.current;
    clearCopyTimer();
    const success = await copyToClipboard(text);
    if (operationId !== copyOperationRef.current) return;
    if (success) {
      setCopied(true);
      copyResetTimeoutRef.current = window.setTimeout(() => {
        if (operationId !== copyOperationRef.current) return;
        setCopied(false);
        copyResetTimeoutRef.current = null;
      }, 2000);
      toast.success(successMessage);
    } else {
      setCopied(false);
      toast.error(failureMessage);
    }
  };

  // ========================================================================
  // Code snippets
  // ========================================================================

  const embedUrl = `${window.location.origin}/embed/apps/${appSlug}`;

  const iframeSnippet = `<iframe
  src="${embedUrl}?param1=value1&hmac=COMPUTED_HMAC"
  style="width: 100%; height: 600px; border: none;"
  allow="clipboard-write"
></iframe>`;

  // ========================================================================
  // Render
  // ========================================================================

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            invalidateCopyOperation();
            setCopied(false);
          }
          onOpenChange(nextOpen);
        }}
      >
        <DialogContent className="w-[calc(100vw-1rem)] max-h-[92dvh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Embed Settings</DialogTitle>
            <DialogDescription>
              Manage secrets for HMAC-authenticated iframe embedding.
            </DialogDescription>
          </DialogHeader>

          <Tabs defaultValue="secrets" className="mt-2 min-w-0">
            <TabsList className="grid min-h-14 w-full grid-cols-2">
              <TabsTrigger className="min-h-11" value="secrets">Secrets</TabsTrigger>
              <TabsTrigger className="min-h-11" value="guide">Integration Guide</TabsTrigger>
            </TabsList>

            {/* ============ Secrets Tab ============ */}
            <TabsContent value="secrets" className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-muted-foreground">
                  Shared secrets used to verify embed requests via HMAC-SHA256.
                </p>
                <Button
                  size="sm"
                  className="min-h-11 shrink-0"
                  onClick={() => setIsCreateOpen(true)}
                  disabled={isCreating || isDeleting}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Create Secret
                </Button>
              </div>

              {isSecretsLoading ? (
                <div
                  role="status"
                  className="rounded-[var(--bf-radius-control)] border border-[color:var(--bf-info-soft)] bg-[color:var(--bf-info-soft)]/20 px-4 py-8 text-center text-sm text-muted-foreground"
                >
                  Loading embed secrets…
                </div>
              ) : isError ? (
                <div
                  role="alert"
                  className="rounded-[var(--bf-radius-control)] border border-[color:var(--bf-danger-soft)] bg-[color:var(--bf-danger-soft)]/20 px-4 py-4 text-sm text-[var(--bf-danger)]"
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <p>
                      Couldn’t load embed secrets.
                      {error instanceof Error ? ` ${error.message}` : ""}
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      className="min-h-11 shrink-0"
                      onClick={() => void refetch()}
                    >
                      Retry
                    </Button>
                  </div>
                </div>
              ) : secrets.length === 0 ? (
                <div className="rounded-[var(--bf-radius-control)] border border-dashed border-[color:var(--bf-info-soft)] px-4 py-8 text-center text-muted-foreground">
                  <Link className="mx-auto mb-2 h-8 w-8 opacity-50" />
                  <p className="text-sm">No embed secrets configured.</p>
                  <p className="text-xs mt-1">
                    Create a secret to enable iframe embedding.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {secrets.map((secret) => (
                    <div
                      key={secret.id}
                      className="flex flex-col gap-3 rounded-[var(--bf-radius-surface)] border border-border/70 bg-card p-3 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="flex min-w-0 flex-wrap items-center gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium [overflow-wrap:anywhere]">{secret.name}</p>
                          <p className="text-xs text-muted-foreground">
                            Created{" "}
                            {new Date(secret.created_at).toLocaleDateString()}
                          </p>
                        </div>
                        <Badge
                          variant="secondary"
                          className={secret.is_active ? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]" : undefined}
                        >
                          {secret.is_active ? "Active" : "Inactive"}
                        </Badge>
                        <Badge variant="outline">
                          {secret.hmac_scheme === "halopsa"
                            ? "HaloPSA"
                            : "Standard"}
                        </Badge>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="min-h-11"
                          disabled={updatingSecretId === secret.id || isDeleting}
                          onClick={() => void handleToggleActive(secret)}
                        >
                          {updatingSecretId === secret.id
                            ? "Updating..."
                            : secret.is_active
                              ? "Deactivate"
                              : "Activate"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-11"
                          aria-label={`Delete ${secret.name}`}
                          onClick={() => setDeleteTarget(secret)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* ============ Integration Guide Tab ============ */}
            <TabsContent value="guide" className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Embed this app in an iframe using HMAC-signed URLs.
              </p>
              <div className="relative overflow-hidden rounded-[var(--bf-radius-control)] border">
                <SyntaxHighlighter
                  language="html"
                  style={oneDark}
                  wrapLongLines
                  customStyle={{ margin: 0, fontSize: "0.75rem" }}
                >
                  {iframeSnippet}
                </SyntaxHighlighter>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute right-2 top-2 size-11"
                  aria-label="Copy embed snippet"
                  onClick={() =>
                    void handleCopy(
                      iframeSnippet,
                      "Copied embed snippet",
                      "Failed to copy embed snippet",
                    )
                  }
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>

      {/* ============ Create Secret Dialog ============ */}
      <Dialog
        open={isCreateOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && isCreating) return;
          setIsCreateOpen(nextOpen);
        }}
      >
        <DialogContent className="w-[calc(100vw-1rem)] max-h-[92dvh] overflow-y-auto sm:max-w-md">
          <form onSubmit={handleCreate}>
            <DialogHeader>
              <DialogTitle>Create Embed Secret</DialogTitle>
              <DialogDescription>
                Create a shared secret for HMAC-authenticated embedding.
                Provide your own secret or leave blank to auto-generate.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="secret-name">Name</Label>
                <Input
                  id="secret-name"
                  placeholder="e.g., Halo Production"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="secret-value">Secret (optional)</Label>
                <Input
                  id="secret-value"
                  placeholder="Leave blank to auto-generate"
                  value={createSecret}
                  onChange={(e) => setCreateSecret(e.target.value)}
                  className="font-mono text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="secret-scheme">HMAC scheme</Label>
                <Select
                  value={createScheme}
                  onValueChange={(v) => setCreateScheme(v as HmacScheme)}
                >
                  <SelectTrigger id="secret-scheme">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="shopify">Standard</SelectItem>
                    <SelectItem value="halopsa">HaloPSA</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {createScheme === "shopify"
                    ? "Signs all query parameters (recommended for most integrations)."
                    : "Signs only agent_id. Use for HaloPSA Custom Tab embeds."}
                </p>
              </div>
            </div>
            {createError && <p role="alert" className="mb-4 text-sm text-[var(--bf-danger)]">{createError}</p>}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                className="min-h-11"
                onClick={() => setIsCreateOpen(false)}
                disabled={isCreating}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                className="min-h-11"
                disabled={isCreating || !createName.trim()}
              >
                {isCreating ? "Creating..." : "Create Secret"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* ============ One-Time Reveal Dialog ============ */}
      <Dialog
        open={!!revealedSecret}
        onOpenChange={(open) => {
          if (!open) {
            invalidateCopyOperation();
            setCopied(false);
            setRevealedSecret(null);
          }
        }}
      >
        <DialogContent className="w-[calc(100vw-1rem)] max-h-[92dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Check className="h-5 w-5 text-success" />
              Secret Created
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="flex items-start gap-2 rounded-[var(--bf-radius-control)] border border-[color:var(--bf-warning-soft)] bg-[color:var(--bf-warning-soft)]/15 p-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--bf-warning)]" />
              <p className="text-sm">
                Copy this secret now — it will not be shown again.
              </p>
            </div>
            <div className="flex gap-2">
              <code className="flex-1 rounded bg-muted px-3 py-2 font-mono text-sm select-all truncate">
                {revealedSecret?.raw_secret || ""}
              </code>
              <Button
                variant="outline"
                size="icon"
                className="size-11 shrink-0"
                aria-label="Copy raw secret"
                onClick={() =>
                  void handleCopy(
                    revealedSecret?.raw_secret || "",
                    "Copied raw secret",
                    "Failed to copy raw secret",
                  )
                }
              >
                {copied ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button className="min-h-11" onClick={() => { invalidateCopyOperation(); setCopied(false); setRevealedSecret(null); }}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ============ Delete Confirmation ============ */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) {
            if (isDeleting) return;
            setDeleteTarget(null);
            setDeleteError(null);
            setIsDeleting(false);
          }
        }}
      >
        <AlertDialogContent className="w-[calc(100vw-1rem)] max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete embed secret?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &quot;{deleteTarget?.name}&quot;.
              Any integrations using this secret will stop working.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <div
              role="alert"
              className="rounded-[var(--bf-radius-control)] border border-[color:var(--bf-danger-soft)] bg-[color:var(--bf-danger-soft)]/20 px-3 py-2 text-sm text-[var(--bf-danger)]"
            >
              {deleteError}
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel
              className="min-h-11"
              onClick={() => setDeleteError(null)}
              disabled={isDeleting}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="min-h-11"
              onClick={(e) => {
                e.preventDefault();
                if (!isDeleting) void handleDelete();
              }}
              disabled={isDeleting}
            >
              {isDeleting ? "Deleting..." : deleteError ? "Retry delete" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
