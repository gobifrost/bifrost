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

from src.core.security import decrypt_secret
from src.services.webhooks.protocol import (
    Deliver,
    HandleResult,
    Rejected,
    RenewResult,
    SubscribeResult,
    ValidationResponse,
    WebhookAdapter,
    WebhookRequest,
)

logger = logging.getLogger(__name__)


def _get_access_token(integration: Any | None) -> str:
    """Read a Graph access token from SDK-style or ORM-style integration data."""
    if not integration:
        raise ValueError("Microsoft integration with OAuth is required")

    oauth = getattr(integration, "oauth", None)
    access_token = getattr(oauth, "access_token", None)
    if access_token:
        return access_token

    provider = getattr(integration, "oauth_provider", None)
    tokens = getattr(provider, "tokens", None) if provider else None
    token = next(iter(tokens or []), None)
    encrypted_access_token = getattr(token, "encrypted_access_token", None)
    if encrypted_access_token:
        raw_token = (
            encrypted_access_token.decode()
            if isinstance(encrypted_access_token, bytes)
            else encrypted_access_token
        )
        return decrypt_secret(raw_token)

    raise ValueError("OAuth access token is required")


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
        include_resource_data: Whether to include resource data in notifications
    """

    name = "microsoft_graph"
    display_name = "Microsoft Graph"
    description = "Webhooks for Microsoft 365 services (Mail, Calendar, Teams, etc.)"
    requires_integration = "Microsoft"
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
                    "label_path": "displayName",
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
            "include_resource_data": {
                "type": "boolean",
                "title": "Include Resource Data",
                "description": "Include the changed resource data in notifications (requires additional permissions)",
                "default": False,
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
        access_token = _get_access_token(integration)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://graph.microsoft.com/v1.0/users",
                headers={"Authorization": f"Bearer {access_token}"},
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

            # Return list of user objects with id and displayName
            return [
                {
                    "id": user["id"],
                    "displayName": user.get("displayName") or user.get("userPrincipalName", "Unknown"),
                    "mail": user.get("mail"),
                }
                for user in users
            ]

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
        access_token = _get_access_token(integration)

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

        # Add resource data if requested (requires encryption certificate)
        if config.get("include_resource_data"):
            subscription_body["includeResourceData"] = True
            # Note: Would need to add encryption certificate handling

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=subscription_body,
                timeout=30.0,
            )

            if response.status_code == 201:
                data = response.json()
                return SubscribeResult(
                    external_id=data["id"],
                    state={"client_state": client_state},
                    expires_at=self.parse_datetime(data["expirationDateTime"]),
                )
            else:
                error_msg = response.text
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = error_data["error"].get("message", error_msg)
                except (ValueError, KeyError) as e:
                    # Response wasn't JSON or didn't have expected shape — fall back to raw text
                    logger.debug(f"could not parse Graph subscribe error response as JSON: {e}")
                raise ValueError(f"Failed to create Graph subscription: {error_msg}")

    async def unsubscribe(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> None:
        """
        Delete Graph subscription.

        Best effort - doesn't raise on failure.
        """
        if not external_id:
            return

        try:
            access_token = _get_access_token(integration)
        except ValueError:
            return

        try:
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"https://graph.microsoft.com/v1.0/subscriptions/{external_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30.0,
                )
        except Exception:
            # Best effort - subscription may have already expired
            pass

    async def renew(
        self,
        external_id: str | None,
        state: dict[str, Any],
        integration: Any | None,
    ) -> RenewResult | None:
        """
        Renew Graph subscription.

        Called periodically to extend subscription before expiration.
        """
        if not external_id:
            return None

        try:
            access_token = _get_access_token(integration)
        except ValueError:
            return None

        # Extend for another 3 days
        new_expiration = self.expiration_datetime(days=3)

        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://graph.microsoft.com/v1.0/subscriptions/{external_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"expirationDateTime": new_expiration},
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                return RenewResult(
                    expires_at=self.parse_datetime(data["expirationDateTime"]),
                )
            else:
                # Subscription may have expired - caller should recreate
                return None

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

        # Extract event type from change type
        event_type = first_notification.get("changeType")
        resource = first_notification.get("resource")
        if resource:
            # Create more descriptive event type
            # e.g., "messages.created", "events.updated"
            resource_type = resource.split("/")[-1] if "/" in resource else resource
            if event_type:
                event_type = f"{resource_type}.{event_type}"

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
