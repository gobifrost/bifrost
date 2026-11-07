import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useOrganizations } from "@/hooks/useOrganizations";
import { getErrorMessage } from "@/lib/api-error";
import * as githubApp from "@/services/githubApp";
import { observePlatformJob } from "@/services/platformJobs";
import { webSocketService } from "@/services/websocket";

export function GitHubAppSettings() {
    const client = useQueryClient();
    const config = useQuery({ queryKey: ["github-app", "config"], queryFn: githubApp.getGitHubApp });
    const organizations = useOrganizations();
    const [organizationId, setOrganizationId] = useState("");
    const [appId, setAppId] = useState<string | null>(null);
    const [key, setKey] = useState("");
    const [secret, setSecret] = useState("");
    const [installationId, setInstallationId] = useState("");
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [jobId, setJobId] = useState<string | null>(null);
    const [jobMessage, setJobMessage] = useState<string | null>(null);
    const mounted = useRef(true);
    const inventory = useQuery({
        queryKey: ["github-app", "installations", organizationId],
        queryFn: () => githubApp.listGitHubAppInstallations(organizationId),
        enabled: !!organizationId,
    });

    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    useEffect(() => {
        if (!jobId) return;
        let active = true;
        const update = (job: { status: string; error?: { message: string } | null; progress?: { phase?: string | null } }) => {
            if (!active) return;
            setJobMessage(job.error?.message ?? job.progress?.phase ?? job.status);
            if (["succeeded", "failed", "cancelled", "requires_action"].includes(job.status)) {
                void client.invalidateQueries({ queryKey: ["github-app"] });
                setJobId(null);
            }
        };
        const unsubscribe = webSocketService.onPlatformJobUpdate(jobId, update);
        const observation = observePlatformJob(jobId, update);
        void observation.promise.catch(() => {
            if (active) setJobMessage("The status snapshot is unavailable. Progress remains available in notifications.");
        });
        return () => { active = false; unsubscribe(); observation.cancel(); };
    }, [jobId, client]);

    async function perform(action: () => Promise<unknown>) {
        setPending(true);
        setError(null);
        try {
            await action();
            if (mounted.current) await client.invalidateQueries({ queryKey: ["github-app"] });
        } catch (failure) {
            if (mounted.current) setError(getErrorMessage(failure, "GitHub App operation failed"));
        } finally {
            if (mounted.current) setPending(false);
        }
    }

    if (config.isPending) return <p role="status">Loading GitHub App configuration…</p>;
    if (config.isError) return <div role="alert">{getErrorMessage(config.error, "Cannot read GitHub App configuration")} <Button onClick={() => void config.refetch()}>Retry</Button></div>;
    const saved = config.data;

    return <div className="space-y-6">
        <Card>
            <CardHeader>
                <CardTitle>GitHub App</CardTitle>
                <CardDescription>Connect your deployment-owned App for installation-scoped events and read-only inventory. Repository Git sync keeps its separate PAT configuration.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <p>Manually register and install your own GitHub App, then configure its credentials here. Registration ownership, GitHub installation consent, and Bifrost organization access are separate.</p>
                <p>Use Metadata: Read. Enable Issues, Pull Requests, Checks, or Contents: Read only for the event families you need. Configure an HTTPS webhook URL and the same secret in GitHub and Bifrost.</p>
                {saved?.webhook_path && <p>Webhook Path: <code className="break-all">{saved.webhook_path}</code></p>}
                <p>{saved?.configured ? `Configured: ${saved.slug}. Key and webhook secret are stored; connection health requires verified inventory and delivery evidence.` : "No App is configured."}</p>
                <form className="space-y-3" onSubmit={(event) => {
                    event.preventDefault();
                    void perform(async () => {
                        await githubApp.configureGitHubApp({ app_id: Number(appId ?? saved?.app_id), private_key: key || undefined, webhook_secret: secret || undefined });
                        setKey(""); setSecret("");
                    });
                }}>
                    <Label htmlFor="github-app-id">App ID</Label>
                    <Input id="github-app-id" inputMode="numeric" value={appId ?? String(saved?.app_id ?? "")} onChange={(event) => setAppId(event.target.value)} required />
                    <Label htmlFor="github-app-key">Private Key</Label>
                    <Textarea id="github-app-key" value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" spellCheck={false} placeholder={saved?.private_key_set ? "Leave blank to retain the stored key" : "Paste the RSA PEM private key"} required={!saved?.private_key_set} />
                    <Label htmlFor="github-app-secret">Webhook Secret</Label>
                    <Input id="github-app-secret" type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} minLength={32} required={!saved?.webhook_secret_set} />
                    <div className="flex flex-wrap gap-2">
                        <Button type="submit" disabled={pending}>Verify And Save App</Button>
                        {saved?.configured && <Button type="button" variant="outline" disabled={pending} onClick={() => void perform(githubApp.disableGitHubApp)}>Disconnect App</Button>}
                    </div>
                </form>
                {error && <p role="alert">{error}</p>}
            </CardContent>
        </Card>
        {saved?.configured && <Card>
            <CardHeader><CardTitle>Installations And Health</CardTitle><CardDescription>Each installation belongs to one Bifrost organization. Refresh inventory and recover missed deliveries from this deployment.</CardDescription></CardHeader>
            <CardContent className="space-y-4">
                <p>Last Reconciliation: {saved.last_reconciled_at ? new Date(saved.last_reconciled_at).toLocaleString() : "Not observed"}</p>
                {saved.recovery_gap && <p role="alert">A delivery history gap or uncertain redelivery needs operator review. Reconciliation does not guarantee complete event replay.</p>}
                <Button disabled={pending || !!jobId} onClick={() => void perform(async () => {
                    await webSocketService.connect();
                    const accepted = await githubApp.reconcileGitHubApp();
                    setJobId(accepted.job_id); setJobMessage("Reconciliation queued. Progress is also available in notifications.");
                })}>Refresh And Recover</Button>
                {jobMessage && <p role="status">{jobMessage}</p>}
                <Label htmlFor="github-app-organization">Bifrost Organization</Label>
                <select id="github-app-organization" className="w-full rounded-md border bg-background p-2" value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}>
                    <option value="">Select An Organization</option>
                    {organizations.data?.map((organization) => <option key={organization.id} value={organization.id}>{organization.name}</option>)}
                </select>
                {organizations.isError && <p role="alert">Could not load organizations.</p>}
                <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
                    event.preventDefault();
                    void perform(async () => {
                        await githubApp.bindGitHubAppInstallation({ installation_id: Number(installationId), organization_id: organizationId });
                        setInstallationId("");
                    });
                }}>
                    <div className="space-y-2"><Label htmlFor="github-installation-id">Installation ID</Label><Input id="github-installation-id" inputMode="numeric" value={installationId} onChange={(event) => setInstallationId(event.target.value)} required /></div>
                    <Button type="submit" disabled={pending || !organizationId}>Verify And Bind Installation</Button>
                </form>
                <p>After binding, choose Refresh And Recover to verify repository inventory. Bindings remain inactive until verification succeeds.</p>
                {inventory.isError && <p role="alert">{getErrorMessage(inventory.error, "Could not load installation inventory")}</p>}
                <div className="max-h-[60vh] space-y-4 overflow-auto">
                    {inventory.data?.installations.map((installation) => <section key={installation.id} className="space-y-2 rounded-md border p-4" aria-label={`Installation ${installation.installation_id}`}>
                        <h3 className="font-semibold">{installation.account_login} — {installation.status}</h3>
                        <p>Event Source: <code>{installation.event_source_id}</code></p>
                        <p>Verified: {installation.verified_at ? new Date(installation.verified_at).toLocaleString() : "Not observed"}; Last Delivery: {installation.last_delivery_at ? new Date(installation.last_delivery_at).toLocaleString() : "Not observed"}</p>
                        <p>Granted Permissions: {Object.entries(installation.permissions).map(([name, level]) => `${name}: ${level}`).join(", ")}</p>
                        <p>Available Events: {installation.available_events.join(", ") || "None"}</p>
                        <p>Quota Remaining: {installation.quota_remaining ?? "Not observed"}{installation.quota_reset ? `; Reset: ${new Date(installation.quota_reset * 1000).toLocaleString()}` : ""}</p>
                        {installation.retry_after && <p role="status">GitHub requests are paused for {installation.retry_after} seconds by provider rate limits.</p>}
                        {installation.last_error && <p role="alert">{installation.last_error}</p>}
                        <ul className="list-inside list-disc">{installation.repositories.map((repository) => <li key={repository.id}>{repository.full_name}</li>)}</ul>
                        <Button variant="outline" disabled={pending || installation.status === "disconnected"} onClick={() => void perform(() => githubApp.disconnectGitHubAppInstallation(installation.id))}>Disconnect Installation</Button>
                    </section>)}
                </div>
                <p>Use the installation’s event source with existing Event subscriptions. Example types: github.pull_request.opened, github.check_run.completed, and github.release.published.</p>
            </CardContent>
        </Card>}
    </div>;
}
