import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ GET: vi.fn(), PUT: vi.fn(), POST: vi.fn(), DELETE: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiClient: mocks }));
import * as service from "./githubApp";

describe("GitHub App service", () => {
    beforeEach(() => { Object.values(mocks).forEach((mock) => mock.mockReset()); });

    it("uses the explicit configuration and installation contracts", async () => {
        Object.values(mocks).forEach((mock) => mock.mockResolvedValue({ data: { configured: true } }));
        await service.getGitHubApp();
        expect(mocks.GET).toHaveBeenCalledWith("/api/github-app/config");
        const body = { app_id: 123, private_key: "pem", webhook_secret: "secret" };
        await service.configureGitHubApp(body);
        expect(mocks.PUT).toHaveBeenCalledWith("/api/github-app/config", { body });
        await service.disableGitHubApp();
        expect(mocks.DELETE).toHaveBeenCalledWith("/api/github-app/config");
        await service.listGitHubAppInstallations("organization-1");
        expect(mocks.GET).toHaveBeenCalledWith("/api/github-app/installations", { params: { query: { organization_id: "organization-1" } } });
        const binding = { installation_id: 456, organization_id: "organization-1" };
        await service.bindGitHubAppInstallation(binding);
        expect(mocks.POST).toHaveBeenCalledWith("/api/github-app/installations", { body: binding });
        await service.disconnectGitHubAppInstallation("binding-1");
        expect(mocks.DELETE).toHaveBeenCalledWith("/api/github-app/installations/{binding_id}", { params: { path: { binding_id: "binding-1" } } });
        await service.reconcileGitHubApp();
        expect(mocks.POST).toHaveBeenCalledWith("/api/github-app/reconcile");
    });

    it.each([
        ["GET", () => service.getGitHubApp()],
        ["PUT", () => service.configureGitHubApp({ app_id: 1 })],
        ["DELETE", () => service.disableGitHubApp()],
        ["GET", () => service.listGitHubAppInstallations("organization-1")],
        ["POST", () => service.bindGitHubAppInstallation({ installation_id: 1, organization_id: "organization-1" })],
        ["DELETE", () => service.disconnectGitHubAppInstallation("binding-1")],
        ["POST", () => service.reconcileGitHubApp()],
    ] as const)("preserves provider refusal through %s without another credential path", async (method, call) => {
        mocks[method].mockResolvedValue({ error: { detail: "Installation is suspended" } });
        await expect(call()).rejects.toThrow("Installation is suspended");
        expect(Object.values(mocks).reduce((sum, mock) => sum + mock.mock.calls.length, 0)).toBe(1);
    });
});
