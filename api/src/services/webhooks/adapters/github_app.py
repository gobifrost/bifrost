"""Display GitHub App event sources while refusing the generic ingress path."""

from typing import Any

from src.services.webhooks.protocol import Rejected, SubscribeResult, WebhookAdapter, WebhookRequest


class GitHubAppAdapter(WebhookAdapter):
    name = "github_app"
    display_name = "GitHub App"
    description = "Verified installation events received through the deployment GitHub App webhook."
    requires_organization = True

    async def subscribe(self, callback_url: str, config: dict[str, Any], integration: Any | None) -> SubscribeResult:
        raise ValueError("Create GitHub App sources by binding a verified installation in GitHub settings")

    async def unsubscribe(self, external_id: str | None, state: dict[str, Any], integration: Any | None) -> None:
        return None

    async def handle_request(self, request: WebhookRequest, config: dict[str, Any], state: dict[str, Any]) -> Rejected:
        return Rejected("Use the verified App-level GitHub webhook endpoint", 404)
