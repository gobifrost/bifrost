import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
    getGitHubApp: vi.fn(), configureGitHubApp: vi.fn(), disableGitHubApp: vi.fn(),
    listGitHubAppInstallations: vi.fn(), bindGitHubAppInstallation: vi.fn(),
    disconnectGitHubAppInstallation: vi.fn(), reconcileGitHubApp: vi.fn(),
    unsubscribe: vi.fn(), cancel: vi.fn(), observe: vi.fn(), subscribe: vi.fn(),
}));
vi.mock("@/services/githubApp", () => mocks);
vi.mock("@/hooks/useOrganizations", () => ({ useOrganizations: () => ({ data: [{ id: "org-1", name: "First Organization" }] }) }));
vi.mock("@/services/platformJobs", () => ({ observePlatformJob: mocks.observe }));
vi.mock("@/services/websocket", () => ({ webSocketService: { connect: vi.fn().mockResolvedValue(undefined), onPlatformJobUpdate: mocks.subscribe } }));
import { GitHubAppSettings } from "./GitHubAppSettings";

function show() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    return render(<QueryClientProvider client={queryClient}><GitHubAppSettings /></QueryClientProvider>);
}

describe("GitHub App settings", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.getGitHubApp.mockResolvedValue({ configured: false, private_key_set: false, webhook_secret_set: false });
        mocks.listGitHubAppInstallations.mockResolvedValue({ installations: [] });
        mocks.configureGitHubApp.mockResolvedValue({});
        mocks.subscribe.mockReturnValue(mocks.unsubscribe);
        mocks.observe.mockReturnValue({ cancel: mocks.cancel, promise: Promise.resolve(undefined) });
    });

    it("submits explicit credentials and clears them after saving", async () => {
        const user = userEvent.setup();
        show();
        await screen.findByLabelText("App ID");
        await user.type(screen.getByLabelText("App ID"), "123");
        await user.type(screen.getByLabelText("Private Key"), "synthetic-pem");
        await user.type(screen.getByLabelText("Webhook Secret"), "s".repeat(32));
        await user.click(screen.getByRole("button", { name: "Verify And Save App" }));
        await waitFor(() => expect(mocks.configureGitHubApp).toHaveBeenCalledWith({ app_id: 123, private_key: "synthetic-pem", webhook_secret: "s".repeat(32) }));
        expect(screen.getByLabelText("Private Key")).toHaveValue("");
        expect(screen.getByLabelText("Webhook Secret")).toHaveValue("");
    });

    it("stops on configuration denial before showing secret controls", async () => {
        mocks.getGitHubApp.mockRejectedValue(new Error("Permission denied"));
        show();
        expect(await screen.findByRole("alert")).toHaveTextContent("Permission denied");
        expect(screen.queryByLabelText("Private Key")).not.toBeInTheDocument();
    });

    it.each(["Installation is suspended", "GitHub rate limit reached", "Webhook verification is not configured"])("shows %s without credential substitution", async (message) => {
        const user = userEvent.setup();
        mocks.getGitHubApp.mockResolvedValue({ configured: true, app_id: 123, slug: "test-app", private_key_set: true, webhook_secret_set: true });
        mocks.configureGitHubApp.mockRejectedValue(new Error(message));
        show();
        await user.click(await screen.findByRole("button", { name: "Verify And Save App" }));
        expect(await screen.findByRole("alert")).toHaveTextContent(message);
        expect(mocks.configureGitHubApp).toHaveBeenCalledWith({ app_id: 123, private_key: undefined, webhook_secret: undefined });
    });

    it("uses shared job observation and cleans up on navigation", async () => {
        const user = userEvent.setup();
        mocks.getGitHubApp.mockResolvedValue({ configured: true, app_id: 123, private_key_set: true, webhook_secret_set: true });
        mocks.reconcileGitHubApp.mockResolvedValue({ job_id: "job-1", status: "queued" });
        const rendered = show();
        await user.click(await screen.findByRole("button", { name: "Refresh And Recover" }));
        await waitFor(() => expect(mocks.observe).toHaveBeenCalledWith("job-1", expect.any(Function)));
        rendered.unmount();
        expect(mocks.unsubscribe).toHaveBeenCalled();
        expect(mocks.cancel).toHaveBeenCalled();
    });
});
