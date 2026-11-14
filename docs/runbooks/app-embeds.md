# App Embeds

## HaloPSA Ticket Context

Configure the HaloPSA custom tab to load the App's embed URL with its agent
signature and an optional ticket ID:

```text
https://<bifrost-host>/embed/apps/<app-slug>?agent_id=$LOGGEDINAGENTID&hmac=$_HMAC&ticket_id=$FAULTID
```

Use the HaloPSA HMAC scheme on the App's embed secret. Bifrost verifies the
agent signature, then redirects to the App with `ticket_id` in the query
string. The App can read it with `new URLSearchParams(window.location.search).get("ticket_id")`
and pass it explicitly as a workflow input. The ticket ID does not appear
automatically in workflow inputs or in verified `context.embed`.

Only `ticket_id` is forwarded to the App query string. The HMAC, agent ID,
and other parameters are not forwarded. Requests without a ticket ID keep
their existing redirect behavior.

Halo's signature covers only the agent ID. Treat `ticket_id` as editable user
input: validate it and check record access in the App's workflow before reading
or changing a ticket. Embedded executions use the organization's default
identity (or the global default for an App without an organization), not the
viewing Halo agent's Bifrost identity. A ticket ID is a record selector, not
proof that the viewer may access that ticket. Verify the ticket's customer
against the intended organization when using a shared Halo integration.

Verified `context.embed` still contains only `agent_id` for HaloPSA. Shopify
embeds retain their existing signature verification over all query parameters;
their verified context is unchanged. Query-string values remain editable
after the redirect regardless of the original signing scheme.
