"""
Microsoft Graph webhook adapter for the Bifrost event system.

Handles Graph API subscription lifecycle:
- Creating subscriptions with change notification URLs
- Responding to validation callbacks
- Renewing subscriptions before expiration
- Validating client state on incoming notifications
"""

import logging
from datetime import timedelta
from typing import Any

import httpx

from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    RenewResult,
    SubscribeResult,
    ValidationResponse,
    WebhookAdapter,
    WebhookIntegrationAuth,
    WebhookRequest,
)

logger = logging.getLogger(__name__)


class MicrosoftGraphAdapter(WebhookAdapter):
    """
    Microsoft Graph webhook adapter.

    Handles Graph API subscription lifecycle including:
    - Creating subscriptions via POST /subscriptions
    - Responding to validationToken challenges
    - Renewing subscriptions before 72-hour expiration
    - Validating clientState on incoming notifications

    Requires: Microsoft integration with Graph API permissions

    Configuration:
        resource: Graph resource path (e.g., '/users/{user-id}/messages')
        change_types: List of change types to subscribe to
    """

    name = "microsoft_graph"
    display_name = "Microsoft Graph"
    description = "Webhooks for Microsoft 365 services (Mail, Calendar, Teams, etc.)"
    requires_integration = "Microsoft"
    requires_organization = True
    renewal_interval = timedelta(hours=24)  # Check daily, renew before 72h expiry

    config_schema = {
        "type": "object",
        "required": ["resource", "change_types"],
        "properties": {
            "user_id": {
                "type": "string",
                "title": "User",
                "description": "Select a user for user-specific resources",
                "x-dynamic-values": {
                    "operation": "list_users",
                    "value_path": "id",
                    "label_path": "label",
                    "depends_on": [],
                },
            },
            "resource": {
                "type": "string",
                "title": "Resource Type",
                "description": "Graph resource to subscribe to",
                "x-dynamic-values": {
                    "operation": "list_resources",
                    "value_path": "value",
                    "label_path": "label",
                    "depends_on": ["user_id"],
                },
            },
            "change_types": {
                "type": "array",
                "title": "Change Types",
                "description": "Types of changes to subscribe to",
                "items": {
                    "type": "string",
                    "enum": ["created", "updated", "deleted"],
                },
                "default": ["created"],
                "uniqueItems": True,
            },
        },
    }

    async def get_dynamic_values(
        self,
        operation: str,
        integration: Any | None,
        current_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Fetch dynamic options for config fields.

        Supported operations:
        - list_users: Fetch users from Graph API
        - list_resources: Return common resource templates based on selected user
        """
        if operation == "list_users":
            return await self._list_users(integration)
        elif operation == "list_resources":
            return self._list_resources(current_config)
        else:
            raise NotImplementedError(f"Operation '{operation}' not supported by {self.name}")

    async def _list_users(self, integration: Any | None) -> list[dict[str, Any]]:
        """Fetch users from Microsoft Graph API."""
        auth = self._require_auth(integration)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://graph.microsoft.com/v1.0/users",
                headers={"Authorization": f"Bearer {auth.access_token}"},
                params={"$select": "id,displayName,mail,userPrincipalName", "$top": "100"},
                timeout=30.0,
            )

            if response.status_code != 200:
                error_msg = response.text
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = error_data["error"].get("message", error_msg)
                except (ValueError, KeyError) as e:
                    # Response wasn't JSON or didn't have expected shape — fall back to raw text
                    logger.debug(f"could not parse Graph error response as JSON: {e}")
                raise ValueError(f"Failed to fetch users: {error_msg}")

            data = response.json()
            users = data.get("value", [])

            return [self._user_option(user) for user in users]

    @staticmethod
    def _user_option(user: dict[str, Any]) -> dict[str, Any]:
        display_name = user.get("displayName")
        principal_name = user.get("userPrincipalName") or user.get("mail")
        if principal_name and display_name and principal_name != display_name:
            label = f"{principal_name} · {display_name}"
        else:
            label = principal_name or display_name or "Unknown user"
        return {
            "id": user["id"],
            "label": label,
            "displayName": display_name,
            "userPrincipalName": user.get("userPrincipalName"),
            "mail": user.get("mail"),
        }

    async def _fetch_user_metadata(
        self,
        client: httpx.AsyncClient,
        auth: WebhookIntegrationAuth,
        user_id: str,
    ) -> dict[str, Any]:
        response = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{user_id}",
            headers={"Authorization": f"Bearer {auth.access_token}"},
            params={"$select": "id,displayName,mail,userPrincipalName"},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise ValueError(
                "Failed to resolve the selected Graph user: "
                f"{self._error_message(response)}"
            )
        user = response.json()
        return {
            "user_display_name": user.get("displayName"),
            "user_principal_name": user.get("userPrincipalName"),
            "user_mail": user.get("mail"),
        }

    def _list_resources(self, current_config: dict[str, Any]) -> list[dict[str, Any]]:
        """Return common Graph resource templates based on selected user."""
        user_id = current_config.get("user_id", "{user-id}")

        # Common user-specific resources
        resources = [
            {
                "value": f"/users/{user_id}/messages",
                "label": "Mail Messages",
                "description": "Notifications for new, updated, or deleted emails",
            },
            {
                "value": f"/users/{user_id}/events",
                "label": "Calendar Events",
                "description": "Notifications for calendar event changes",
            },
            {
                "value": f"/users/{user_id}/mailFolders",
                "label": "Mail Folders",
                "description": "Notifications for mail folder changes",
            },
            {
                "value": f"/users/{user_id}/contacts",
                "label": "Contacts",
                "description": "Notifications for contact changes",
            },
        ]

        # Add some global resources that don't require user selection
        global_resources = [
            {
                "value": "/communications/callRecords",
                "label": "Call Records (Global)",
                "description": "Notifications for call recordings (requires CallRecords.Read.All)",
            },
        ]

        return resources + global_resources

    async def subscribe(
        self,
        callback_url: str,
        config: dict[str, Any],
        integration: Any | None,
    ) -> SubscribeResult:
        """
        Create Graph subscription.

        Calls POST /subscriptions to register the webhook with Graph API.
        """
        auth = self._require_auth(integration)

        # Generate client state for validation
        client_state = self.generate_secret(32)

        # Build subscription request
        # Graph subscriptions expire in max 72 hours for most resources
        expiration = self.expiration_datetime(days=3)

        subscription_body: dict[str, Any] = {
            "changeType": ",".join(config.get("change_types", ["created"])),
            "notificationUrl": callback_url,
            "resource": config["resource"],
            "expirationDateTime": expiration,
            "clientState": client_state,
        }

        async with httpx.AsyncClient() as client:
            user_metadata: dict[str, Any] = {}
            user_id = config.get("user_id")
            if user_id:
                user_metadata = await self._fetch_user_metadata(client, auth, user_id)

            response = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers={
                    "Authorization": f"Bearer {auth.access_token}",
                    "Content-Type": "application/json",
                },
                json=subscription_body,
                timeout=30.0,
            )

            if response.status_code == 201:
                data = response.json()
                return SubscribeResult(
                    external_id=data["id"],
                    state={"client_state": client_state, **user_metadata},
                    expires_at=self.parse_datetime(data["expirationDateTime"]),
                )
            else:
                raise ValueError(
                    "Failed to create Graph subscription: "
                    f"{self._error_message(response)}"
                )

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        """
        Delete Graph subscription.

        A missing subscription is already clean. Other provider failures are
        raised so Bifrost does not delete its only record of an orphan.
        """
        if not external_id:
            return

        auth = self._require_auth(integration)

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"https://graph.microsoft.com/v1.0/subscriptions/{external_id}",
                headers={"Authorization": f"Bearer {auth.access_token}"},
                timeout=30.0,
            )
        if response.status_code not in {204, 404}:
            raise ValueError(
                "Failed to delete Graph subscription: "
                f"{self._error_message(response)}"
            )

    def get_public_metadata(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": "Microsoft Graph",
            "resource": config.get("resource"),
            "change_types": config.get("change_types", []),
            "user_id": config.get("user_id"),
            "user_display_name": state.get("user_display_name"),
            "user_principal_name": state.get("user_principal_name"),
            "user_mail": state.get("user_mail"),
        }

    async def renew(
        self,
        external_id: str | None,
        state: dict[str, Any],
        config: dict[str, Any],
        integration: Any | None,
    ) -> RenewResult | None:
        """
        Renew Graph subscription.

        Called periodically to extend subscription before expiration.
        """
        if not external_id:
            return None

        if not isinstance(integration, WebhookIntegrationAuth):
            return None

        # Extend for another 3 days
        new_expiration = self.expiration_datetime(days=3)

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://graph.microsoft.com/v1.0/subscriptions/{external_id}",
                headers={
                    "Authorization": f"Bearer {integration.access_token}",
                    "Content-Type": "application/json",
                },
                json={"expirationDateTime": new_expiration},
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                user_metadata: dict[str, Any] = {}
                user_id = config.get("user_id")
                if user_id:
                    user_metadata = await self._fetch_user_metadata(
                        client, integration, user_id
                    )
                return RenewResult(
                    expires_at=self.parse_datetime(data["expirationDateTime"]),
                    state=user_metadata,
                )
            else:
                # Subscription may have expired - caller should recreate
                return None

    @staticmethod
    def _require_auth(integration: Any | None) -> WebhookIntegrationAuth:
        if not isinstance(integration, WebhookIntegrationAuth):
            raise ValueError(
                "Microsoft integration authentication could not be resolved for this organization"
            )
        return integration

    async def handle_request(
        self,
        request: WebhookRequest,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> HandleResult:
        """
        Handle incoming Graph notification.

        Handles both:
        - Validation callbacks (GET with validationToken)
        - Change notifications (POST with notification payload)
        """
        # Handle validation callback
        # Graph sends GET request with validationToken query param
        validation_token = request.query_params.get("validationToken")
        if validation_token:
            return ValidationResponse(
                status_code=200,
                body=validation_token,
                content_type="text/plain",
            )

        # Handle change notification
        payload = request.json_body
        if not payload:
            return Rejected(message="Invalid notification payload", status_code=400)

        # Graph sends notifications in a 'value' array
        notifications = payload.get("value", [])
        if not notifications:
            return Rejected(message="No notifications in payload", status_code=400)

        # Validate client state on each notification
        expected_state = state.get("client_state")
        if expected_state:
            for notification in notifications:
                if notification.get("clientState") != expected_state:
                    return Rejected(message="Invalid client state", status_code=401)

        # For now, deliver the first notification
        # Future: could batch process all notifications
        first_notification = notifications[0]

        # Event identity comes from the configured collection, not Graph's
        # notification resource. The latter ends in the changed object's UUID.
        event_type = first_notification.get("changeType")
        configured_resource = config.get("resource")
        if configured_resource:
            resource_type = configured_resource.rstrip("/").split("/")[-1]
            if event_type:
                event_type = f"graph.{resource_type}.{event_type}"

        return Deliver(
            data={
                "notifications": notifications,
                "subscription_id": first_notification.get("subscriptionId"),
                "resource": first_notification.get("resource"),
                "change_type": first_notification.get("changeType"),
                "client_state": first_notification.get("clientState"),
                "tenant_id": first_notification.get("tenantId"),
                "resource_data": first_notification.get("resourceData"),
            },
            event_type=event_type,
            raw_headers=request.headers,
        )

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        error_msg = response.text
        try:
            error_data = response.json()
            if "error" in error_data:
                error_msg = error_data["error"].get("message", error_msg)
        except (ValueError, KeyError) as exc:
            logger.debug("could not parse Graph error response as JSON: %s", exc)
        return error_msg
