import { apiClient } from "@/lib/api-client";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

export type GitHubAppConfig = components["schemas"]["GitHubAppConfigView"];
export type GitHubAppConfigure = components["schemas"]["GitHubAppConfigure"];
export type GitHubAppBinding = components["schemas"]["GitHubAppBind"];
export type GitHubAppInstallation = components["schemas"]["GitHubAppInstallationView"];

export async function getGitHubApp() {
    const { data, error } = await apiClient.GET("/api/github-app/config");
    if (error) throw new Error(getErrorMessage(error, "Could not read GitHub App configuration"));
    return data;
}

export async function configureGitHubApp(body: GitHubAppConfigure) {
    const { data, error } = await apiClient.PUT("/api/github-app/config", { body });
    if (error) throw new Error(getErrorMessage(error, "Could not configure GitHub App"));
    return data;
}

export async function disableGitHubApp() {
    const { data, error } = await apiClient.DELETE("/api/github-app/config");
    if (error) throw new Error(getErrorMessage(error, "Could not disconnect GitHub App"));
    return data;
}

export async function listGitHubAppInstallations(organizationId: string) {
    const { data, error } = await apiClient.GET("/api/github-app/installations", {
        params: { query: { organization_id: organizationId } },
    });
    if (error) throw new Error(getErrorMessage(error, "Could not read installation inventory"));
    return data;
}

export async function bindGitHubAppInstallation(body: GitHubAppBinding) {
    const { data, error } = await apiClient.POST("/api/github-app/installations", { body });
    if (error) throw new Error(getErrorMessage(error, "Could not bind installation"));
    return data;
}

export async function disconnectGitHubAppInstallation(bindingId: string) {
    const { data, error } = await apiClient.DELETE("/api/github-app/installations/{binding_id}", {
        params: { path: { binding_id: bindingId } },
    });
    if (error) throw new Error(getErrorMessage(error, "Could not disconnect installation"));
    return data;
}

export async function reconcileGitHubApp() {
    const { data, error } = await apiClient.POST("/api/github-app/reconcile");
    if (error) throw new Error(getErrorMessage(error, "Could not start GitHub App reconciliation"));
    return data;
}
