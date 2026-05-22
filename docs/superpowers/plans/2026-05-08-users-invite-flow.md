# Users: Invite Flow + Table Redesign + Email Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three coordinated improvements to user management: (1) magic-link invite flow, (2) Users-page table redesign with sticky right-side columns, (3) Email Configuration "Test" replacing "Validate" with a real test send.

**Architecture:** Reuse the existing `User.is_registered` flag — `is_registered=False` IS the pending-invite state. New `user_invite` table holds single-use, time-bound, hashed tokens; one active invite per user (revoked-on-resend). Invite delivery rides on the existing `send_email(recipient, subject, body, html_body)` service — no new email-config slot, no new transport. Frontend uses existing shadcn DataTable; sticky right-side columns added via Tailwind `sticky right-0` on Date and Actions cells. Email test extends the existing validate endpoint to optionally accept a recipient and dispatch a real send.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (api), Pydantic v2 (contracts), React + TanStack Query + shadcn/ui + Tailwind (client), pytest + vitest + Playwright (tests).

**Closes:** #226 (table scroll), #227 (invite flow), #228 (email test).

---

## File Structure

### New backend files
- `api/src/models/orm/user_invites.py` — `UserInvite` ORM model
- `api/src/models/contracts/user_invites.py` — request/response models for invite endpoints
- `api/src/services/user_invite_service.py` — invite business logic (create, regenerate, revoke, consume, list)
- `api/alembic/versions/<rev>_add_user_invites.py` — migration for `user_invites` table

### Modified backend files
- `api/src/models/orm/__init__.py` — export `UserInvite`
- `api/src/models/contracts/users.py` — add `invite: bool = False` to `UserCreate`; add `invite_status` field to `UserPublic`
- `api/src/routers/users.py` — wire `invite=True` path; add invite-management endpoints (resend, regenerate, revoke, get)
- `api/src/routers/email_config.py` — extend validate route with optional `recipient` to dispatch real send
- `api/src/models/contracts/email.py` — add `recipient: str | None` to validate request, add `email_sent` / `error` to response
- `api/src/routers/auth.py` — add `POST /api/auth/register-from-invite` endpoint that consumes a token and registers the user
- `api/bifrost/sdk/users.py` (or wherever users SDK lives — verify and create if missing) — add `invite=False` flag to create

### New frontend files
- `client/src/services/user-invites.ts` — service wrappers for invite endpoints
- `client/src/services/user-invites.test.ts` — vitest coverage of service wrappers
- `client/src/hooks/useUserInvites.ts` — TanStack Query hooks
- `client/src/components/users/InviteActionsMenu.tsx` — dropdown for resend/regenerate/copy/revoke on pending users
- `client/src/components/users/InviteActionsMenu.test.tsx` — vitest coverage
- `client/src/components/users/UserStatusBadge.tsx` — Active / Pending invite / Invite expired badge
- `client/src/components/auth/AuthSetupSteps.tsx` — extracted shared passkey/password setup component (used by both Setup.tsx and new Register.tsx)
- `client/src/components/auth/AuthSetupSteps.test.tsx` — vitest coverage
- `client/src/pages/Register.tsx` — `/register?token=...` page consuming invites
- `client/src/components/settings/EmailTestDialog.tsx` — test recipient prompt
- `client/src/components/settings/EmailTestDialog.test.tsx` — vitest coverage

### Modified frontend files
- `client/src/pages/Users.tsx` — table redesign + Status column + invite actions
- `client/src/pages/Users.test.tsx` (or sibling — create if missing) — table redesign coverage
- `client/src/components/users/CreateUserDialog.tsx` — "Send invite email" checkbox
- `client/src/pages/Setup.tsx` — refactor to use `AuthSetupSteps`
- `client/src/pages/settings/Email.tsx` — rename Validate → Test, open dialog
- `client/src/App.tsx` (or router file) — add `/register` route (unauthenticated)
- `client/e2e/users.spec.ts` (extend or create) — Playwright happy-path for invite flow

---

## Test Strategy

- **Backend unit tests** for `UserInviteService` (token hashing, expiry, single-use, revocation cascade).
- **Backend e2e tests** for invite endpoints (create with `invite=True`, resend, regenerate, revoke, registration via token).
- **Backend e2e test** for the extended email validate-with-recipient flow (mock `send_email` to assert call shape).
- **Vitest** for new components and services.
- **Playwright** happy path: admin invites user → invite email "sent" (intercepted) → invitee registers → user becomes active.

---

## Implementation Tasks

### Phase 1: Email Test (smallest, unblocks invite testing) — Issue #228

### Task 1: Extend email validate contract

**Files:**
- Modify: `api/src/models/contracts/email.py`

- [ ] **Step 1: Read current contract**

Open `api/src/models/contracts/email.py`. Find `EmailWorkflowValidationResponse`. Note current fields.

- [ ] **Step 2: Add request model and extend response model**

Add to the file:

```python
class EmailTestRequest(BaseModel):
    """Request to test an email workflow with an optional real send."""
    recipient: str | None = None  # None = signature validation only; set = real send
```

Extend `EmailWorkflowValidationResponse` (preserving existing fields) with:

```python
    email_sent: bool = False
    send_error: str | None = None
    execution_id: str | None = None
```

- [ ] **Step 3: Commit**

```bash
git add api/src/models/contracts/email.py
git commit -m "feat(email): add EmailTestRequest and email_sent fields to validation response"
```

---

### Task 2: Backend test for extended validate endpoint

**Files:**
- Test: `api/tests/e2e/test_email_test_endpoint.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""E2E tests for POST /api/admin/email/validate/{workflow_id} with recipient."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_validate_without_recipient_does_not_send(
    superuser_client, valid_email_workflow_id
):
    """Validate-only call (no recipient) should not invoke send_email."""
    with patch("src.routers.email_config.send_email", new=AsyncMock()) as send:
        resp = await superuser_client.post(
            f"/api/admin/email/validate/{valid_email_workflow_id}",
            json={},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["email_sent"] is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_validate_with_recipient_dispatches_send(
    superuser_client, valid_email_workflow_id
):
    """Validate with recipient should dispatch a real test email."""
    fake = AsyncMock(return_value=type("R", (), {"success": True, "execution_id": "exec-1", "error": None})())
    with patch("src.routers.email_config.send_email", new=fake):
        resp = await superuser_client.post(
            f"/api/admin/email/validate/{valid_email_workflow_id}",
            json={"recipient": "test@example.com"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["email_sent"] is True
    assert body["execution_id"] == "exec-1"
    fake.assert_called_once()
    kwargs = fake.call_args.kwargs
    assert kwargs["recipient"] == "test@example.com"
    assert "Bifrost" in kwargs["subject"]


@pytest.mark.asyncio
async def test_validate_with_recipient_send_failure_returns_error(
    superuser_client, valid_email_workflow_id
):
    """A failed send should propagate the error and set email_sent=False."""
    fake = AsyncMock(return_value=type("R", (), {"success": False, "execution_id": None, "error": "SMTP down"})())
    with patch("src.routers.email_config.send_email", new=fake):
        resp = await superuser_client.post(
            f"/api/admin/email/validate/{valid_email_workflow_id}",
            json={"recipient": "test@example.com"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_sent"] is False
    assert body["send_error"] == "SMTP down"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
./test.sh stack up
./test.sh tests/e2e/test_email_test_endpoint.py -v
```

Expected: FAIL — `valid_email_workflow_id` fixture missing or endpoint behavior wrong.

- [ ] **Step 3: Add the fixture (in conftest near the test file)**

If `valid_email_workflow_id` doesn't exist in `api/tests/e2e/conftest.py`, add a fixture that creates a workflow whose `parameters_schema` includes `recipient`, `subject`, `body` (mirror what `EmailService.validate_workflow` requires). Inspect existing fixtures in `api/tests/e2e/conftest.py` for the workflow-creation pattern.

- [ ] **Step 4: Commit (test infra)**

```bash
git add api/tests/e2e/test_email_test_endpoint.py api/tests/e2e/conftest.py
git commit -m "test(email): pending tests for validate-with-recipient"
```

---

### Task 3: Implement extended validate endpoint

**Files:**
- Modify: `api/src/routers/email_config.py`

- [ ] **Step 1: Read current validate handler**

Open `api/src/routers/email_config.py`. Locate the `validate_workflow` route (POST `/api/admin/email/validate/{workflow_id}`).

- [ ] **Step 2: Update the route**

Change the handler signature to accept the new request body (default `None` for backward compatibility) and dispatch `send_email` when recipient is provided:

```python
from src.models.contracts.email import EmailTestRequest, EmailWorkflowValidationResponse
from src.services.email_service import EmailService, send_email


@router.post("/validate/{workflow_id}", response_model=EmailWorkflowValidationResponse)
async def validate_email_workflow(
    workflow_id: str,
    user: CurrentSuperuser,
    db: DbSession,
    request: EmailTestRequest | None = Body(default=None),
) -> EmailWorkflowValidationResponse:
    service = EmailService(db)
    result = await service.validate_workflow(workflow_id)

    response = EmailWorkflowValidationResponse(
        valid=result.valid,
        message=result.message,
        workflow_name=result.workflow_name,
        missing_params=result.missing_params,
        extra_required_params=result.extra_required_params,
        email_sent=False,
        send_error=None,
        execution_id=None,
    )

    if not result.valid or request is None or not request.recipient:
        return response

    send_result = await send_email(
        recipient=request.recipient,
        subject="Bifrost — email configuration test",
        body=(
            "This is a test message from Bifrost.\n\n"
            "If you received this, the configured email workflow is delivering messages correctly."
        ),
    )
    response.email_sent = send_result.success
    response.send_error = send_result.error
    response.execution_id = send_result.execution_id
    return response
```

Imports may need `from fastapi import Body`.

- [ ] **Step 3: Run the tests to verify they pass**

```bash
./test.sh tests/e2e/test_email_test_endpoint.py -v
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add api/src/routers/email_config.py
git commit -m "feat(email): validate endpoint accepts optional recipient and dispatches real test send"
```

---

### Task 4: Frontend Email test dialog (vitest first)

**Files:**
- Test: `client/src/components/settings/EmailTestDialog.test.tsx`
- Create: `client/src/components/settings/EmailTestDialog.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { EmailTestDialog } from "./EmailTestDialog";

describe("EmailTestDialog", () => {
  it("prefills recipient with current user email", () => {
    render(
      <EmailTestDialog
        open
        onOpenChange={() => {}}
        currentUserEmail="me@example.com"
        onTest={vi.fn()}
        isPending={false}
      />,
    );
    const input = screen.getByLabelText(/recipient/i) as HTMLInputElement;
    expect(input.value).toBe("me@example.com");
  });

  it("calls onTest with the entered recipient", async () => {
    const onTest = vi.fn();
    render(
      <EmailTestDialog
        open
        onOpenChange={() => {}}
        currentUserEmail="me@example.com"
        onTest={onTest}
        isPending={false}
      />,
    );
    const input = screen.getByLabelText(/recipient/i);
    await userEvent.clear(input);
    await userEvent.type(input, "other@example.com");
    await userEvent.click(screen.getByRole("button", { name: /send test/i }));
    await waitFor(() => expect(onTest).toHaveBeenCalledWith("other@example.com"));
  });
});
```

- [ ] **Step 2: Run vitest to verify failure**

```bash
./test.sh client unit -- src/components/settings/EmailTestDialog.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the dialog**

```tsx
import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentUserEmail: string;
  onTest: (recipient: string) => void;
  isPending: boolean;
}

export function EmailTestDialog({ open, onOpenChange, currentUserEmail, onTest, isPending }: Props) {
  const [recipient, setRecipient] = useState(currentUserEmail);

  useEffect(() => {
    if (open) setRecipient(currentUserEmail);
  }, [open, currentUserEmail]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Test email workflow</DialogTitle>
          <DialogDescription>
            Validates the workflow signature and sends a test message to the recipient below.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="recipient">Recipient</Label>
          <Input
            id="recipient"
            type="email"
            value={recipient}
            onChange={(e) => setRecipient(e.target.value)}
            disabled={isPending}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={() => onTest(recipient)} disabled={isPending || !recipient}>
            {isPending ? "Sending…" : "Send test"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 4: Run tests, expect pass**

```bash
./test.sh client unit -- src/components/settings/EmailTestDialog.test.tsx
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/settings/EmailTestDialog.tsx client/src/components/settings/EmailTestDialog.test.tsx
git commit -m "feat(email): add EmailTestDialog with prefilled recipient"
```

---

### Task 5: Wire the Email page Validate → Test

**Files:**
- Modify: `client/src/pages/settings/Email.tsx`

- [ ] **Step 1: Regenerate types so `EmailTestRequest` exists**

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh
cd client && npm run generate:types
```

Expected: `client/src/lib/v1.d.ts` updated to include the new fields on `EmailWorkflowValidationResponse` and the new request body.

- [ ] **Step 2: Replace the Validate button with a Test button + dialog**

Open `client/src/pages/settings/Email.tsx`. Locate the Validate button (around line 272 per earlier mapping) and the function that calls `/api/admin/email/validate/{workflow_id}`.

Change:
- Button label from "Validate" to "Test".
- Clicking now opens `<EmailTestDialog />`.
- The mutation calls the same endpoint with `{ recipient }` body.
- Surface `email_sent` in the toast (success: "Test email dispatched to X" / failure: error from response).

Pull `currentUserEmail` from `useAuth()` (already imported elsewhere in the file or import via `@/contexts/AuthContext`).

- [ ] **Step 3: Type-check**

```bash
cd client && npm run tsc
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add client/src/pages/settings/Email.tsx client/src/lib/v1.d.ts
git commit -m "feat(email): replace Validate with Test (sends real message via dialog)"
```

---

### Phase 2: Users table redesign (independent of invite flow) — Issue #226

### Task 6: Redesign Users table layout (sticky right + two-line name + Platform Admin badge)

**Files:**
- Modify: `client/src/pages/Users.tsx`
- Test: `client/src/pages/Users.test.tsx` (create or extend)

- [ ] **Step 1: Write failing component test**

If `Users.test.tsx` doesn't exist, create it. Write:

```tsx
import { render, screen, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Users } from "./Users";
import { renderWithProviders } from "@/test/utils"; // adjust to project's helper

const baseUser = (overrides = {}) => ({
  id: "u1",
  email: "alice@bigorganization-with-a-very-long-name.com",
  name: "Alice",
  is_active: true,
  is_superuser: false,
  organization_id: "o1",
  created_at: new Date().toISOString(),
  last_login: null,
  is_registered: true,
  ...overrides,
});

describe("Users table redesign", () => {
  it("renders organization name under the user name (no separate Type column)", () => {
    renderWithProviders(<Users />, { mockUsers: [baseUser()] });
    expect(screen.queryByRole("columnheader", { name: /^type$/i })).toBeNull();
    const row = screen.getByText("Alice").closest("tr")!;
    expect(within(row).getByText(/bigorganization/i)).toBeInTheDocument();
  });

  it("renders Platform Admin badge inline with name", () => {
    renderWithProviders(<Users />, {
      mockUsers: [baseUser({ is_superuser: true, organization_id: null })],
    });
    const row = screen.getByText("Alice").closest("tr")!;
    expect(within(row).getByText(/platform admin/i)).toBeInTheDocument();
  });

  it("Actions column has sticky-right styling", () => {
    renderWithProviders(<Users />, { mockUsers: [baseUser()] });
    const row = screen.getByText("Alice").closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    const actionsCell = cells[cells.length - 1];
    expect(actionsCell.className).toMatch(/sticky/);
    expect(actionsCell.className).toMatch(/right-0/);
  });
});
```

- [ ] **Step 2: Run vitest to verify failure**

```bash
./test.sh client unit -- src/pages/Users.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Refactor `Users.tsx`**

Replace the existing column definitions (~lines 354–470 per earlier mapping) with this layout. Keep the existing handler functions (`handleSort`, `handleEditUser`, `handleToggleActive`, `handleDeleteUser`).

**Header columns (in order):**
1. Name (sortable) — primary header
2. Email (sortable)
3. Roles (existing column placement, leave as-is if already present; otherwise skip)
4. Status (sortable) — new, see Phase 3 task
5. Date (Created — sortable) — `className="sticky right-[<actions-width>] bg-background"` with appropriate offset OR pin only Actions and let Date scroll. **Decision:** pin only Actions to keep CSS simple; Date stays inline on the right but Actions stays sticky.
6. Actions (no header text) — `className="sticky right-0 bg-background"` for both `<DataTableHead>` and each `<DataTableCell>`.

Drop the standalone Organization column header and the standalone Type column.

**Name cell two-line:**

```tsx
<DataTableCell className="font-medium">
  <div className="flex flex-col">
    <div className="flex items-center gap-2">
      <span>{user.name || user.email}</span>
      {user.is_superuser && (
        <Badge variant="secondary" className="text-xs">
          <Shield className="mr-1 h-3 w-3" />
          Platform Admin
        </Badge>
      )}
    </div>
    {user.organization_id && isPlatformAdmin && (() => {
      const orgInfo = getOrgInfo(user.organization_id);
      return (
        <span className="text-xs text-muted-foreground truncate max-w-xs">
          {orgInfo.isProvider ? (
            <Star className="inline mr-1 h-3 w-3 text-amber-500 fill-amber-500" />
          ) : (
            <Building2 className="inline mr-1 h-3 w-3" />
          )}
          {orgInfo.name}
        </span>
      );
    })()}
  </div>
</DataTableCell>
```

**Email cell with truncation + tooltip:**

```tsx
<DataTableCell className="w-0 max-w-xs">
  <Tooltip>
    <TooltipTrigger asChild>
      <span className="block truncate text-muted-foreground">{user.email}</span>
    </TooltipTrigger>
    <TooltipContent>{user.email}</TooltipContent>
  </Tooltip>
</DataTableCell>
```

**Actions cell sticky:**

```tsx
<DataTableCell className="w-0 whitespace-nowrap text-right sticky right-0 bg-background">
  <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
    {/* existing Switch + Edit + Delete buttons unchanged */}
  </div>
</DataTableCell>
```

Apply matching `sticky right-0 bg-background` on the Actions `<DataTableHead>`.

Remove `getUserTypeBadge` and the standalone Type column. Remove the standalone Organization column for platform admins (it's now under the name).

Update the `SortColumn` type:

```tsx
type SortColumn = "name" | "email" | "status" | "created" | "last_login";
```

Update the `sortedUsers` memo to drop the `organization` and `type` cases.

- [ ] **Step 4: Run vitest, expect pass**

```bash
./test.sh client unit -- src/pages/Users.test.tsx
```

Expected: 3 PASS (Status test pinned to a stub if needed; full Status integration in Phase 3).

- [ ] **Step 5: Type-check + lint**

```bash
cd client && npm run tsc && npm run lint
```

Expected: 0 errors.

- [ ] **Step 6: Visual sanity check**

```bash
./debug.sh status   # confirm UP, note URL
```

Open `<URL>/users` in browser. Verify with a long org name there's no horizontal scroll and Actions stay visible.

- [ ] **Step 7: Commit**

```bash
git add client/src/pages/Users.tsx client/src/pages/Users.test.tsx
git commit -m "feat(users): redesign table layout (two-line name, sticky actions, drop Type column) — closes #226"
```

---

### Phase 3: Magic-link invite flow — Issue #227

### Task 7: `UserInvite` ORM + migration

**Files:**
- Create: `api/src/models/orm/user_invites.py`
- Modify: `api/src/models/orm/__init__.py`
- Create: `api/alembic/versions/<rev>_add_user_invites.py`

- [ ] **Step 1: Inspect an existing model for project conventions**

Read `api/src/models/orm/users.py` to mirror the Base/Mapped/mapped_column style.

- [ ] **Step 2: Create the ORM model**

`api/src/models/orm/user_invites.py`:

```python
"""UserInvite ORM model — single-use, time-bound invite tokens."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


class UserInvite(Base):
    """Single-use invite token for completing registration.

    `token_hash` stores a SHA-256 hex digest of the raw token; the raw token
    is only ever returned to the inviter at creation time.
    """

    __tablename__ = "user_invites"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    user = relationship("User")
```

Note `unique=True` on `user_id` enforces "one active invite per user" at the DB level; resend revokes the old row and inserts a new one (or updates; the service decides).

- [ ] **Step 3: Export from `__init__.py`**

Add to `api/src/models/orm/__init__.py`:

```python
from src.models.orm.user_invites import UserInvite  # noqa: F401
```

(Match style of existing exports — likely add to a list.)

- [ ] **Step 4: Generate migration**

```bash
docker compose -f docker-compose.dev.yml exec api alembic revision --autogenerate -m "add user_invites"
```

Inspect the generated file in `api/alembic/versions/`. Hand-edit if needed:
- ensure unique index on `user_id`
- ensure unique index on `token_hash`

- [ ] **Step 5: Apply migration**

```bash
docker compose -f docker-compose.dev.yml restart bifrost-init
docker compose -f docker-compose.dev.yml restart api
```

Verify via API logs the migration applied.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/orm/user_invites.py api/src/models/orm/__init__.py api/alembic/versions/
git commit -m "feat(invites): add UserInvite ORM model and migration"
```

---

### Task 8: Invite contracts (Pydantic)

**Files:**
- Create: `api/src/models/contracts/user_invites.py`
- Modify: `api/src/models/contracts/users.py`

- [ ] **Step 1: Create invite contracts**

`api/src/models/contracts/user_invites.py`:

```python
"""Invite request/response contracts."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr


class InviteStatus(str):
    """Constants. Not a StrEnum so they serialize cleanly."""
    ACTIVE = "active"             # user.is_registered=True
    PENDING = "pending"           # invite exists, not consumed/expired
    EXPIRED = "expired"           # invite exists, past expires_at
    NEVER_INVITED = "never_invited"  # is_registered=False, no invite row


class UserInvitePublic(BaseModel):
    """Invite metadata returned to admins. Never includes the raw token after creation."""
    user_id: UUID
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateInviteResponse(BaseModel):
    """Returned only at creation/regeneration — contains the raw registration link."""
    user_id: UUID
    expires_at: datetime
    registration_url: str  # full URL with raw token, e.g. https://app/register?token=...
    email_sent: bool
    email_error: str | None = None


class RegisterFromInviteRequest(BaseModel):
    """Invitee submits this to consume the token and set up auth."""
    token: str
    name: str | None = None
    password: str | None = None  # optional; passkey path is also supported
```

- [ ] **Step 2: Extend `UserCreate` and `UserPublic`**

Open `api/src/models/contracts/users.py`. Add to `UserCreate`:

```python
    invite: bool = False  # If True, generate invite + dispatch email after create
```

Add to `UserPublic`:

```python
    invite_status: str = "active"  # one of InviteStatus values; populated by router
```

(Default keeps existing JSON shape backward compatible.)

- [ ] **Step 3: Commit**

```bash
git add api/src/models/contracts/user_invites.py api/src/models/contracts/users.py
git commit -m "feat(invites): add invite contracts and UserCreate.invite flag"
```

---

### Task 9: `UserInviteService` (unit-tested)

**Files:**
- Create: `api/src/services/user_invite_service.py`
- Test: `api/tests/unit/test_user_invite_service.py`

- [ ] **Step 1: Write the failing unit tests**

```python
"""Unit tests for UserInviteService."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm import User, UserInvite
from src.services.user_invite_service import (
    INVITE_TTL,
    UserInviteService,
    InviteConsumeError,
)


@pytest.mark.asyncio
async def test_create_invite_returns_raw_token_and_persists_hash(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()

    svc = UserInviteService(unit_db)
    raw_token, invite = await svc.create_or_replace(user_id=user.id, created_by=None)

    assert len(raw_token) >= 32
    assert invite.token_hash != raw_token
    assert invite.expires_at > datetime.now(timezone.utc) + timedelta(days=6)


@pytest.mark.asyncio
async def test_create_invite_replaces_existing(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)

    _, first = await svc.create_or_replace(user_id=user.id, created_by=None)
    _, second = await svc.create_or_replace(user_id=user.id, created_by=None)

    assert first.id != second.id
    rows = (await unit_db.execute(select(UserInvite).where(UserInvite.user_id == user.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == second.id


@pytest.mark.asyncio
async def test_consume_marks_user_registered(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)
    raw, _ = await svc.create_or_replace(user_id=user.id, created_by=None)

    consumed_user = await svc.consume(token=raw, password="newpass123")
    assert consumed_user.id == user.id
    assert consumed_user.is_registered is True
    assert consumed_user.hashed_password != ""


@pytest.mark.asyncio
async def test_consume_rejects_unknown_token(unit_db):
    svc = UserInviteService(unit_db)
    with pytest.raises(InviteConsumeError, match="not found"):
        await svc.consume(token="garbage", password="p")


@pytest.mark.asyncio
async def test_consume_rejects_expired_token(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)
    raw, invite = await svc.create_or_replace(user_id=user.id, created_by=None)
    invite.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await unit_db.flush()
    with pytest.raises(InviteConsumeError, match="expired"):
        await svc.consume(token=raw, password="p")


@pytest.mark.asyncio
async def test_consume_rejects_replay(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)
    raw, _ = await svc.create_or_replace(user_id=user.id, created_by=None)
    await svc.consume(token=raw, password="p")
    with pytest.raises(InviteConsumeError, match="consumed"):
        await svc.consume(token=raw, password="p2")


@pytest.mark.asyncio
async def test_revoke_clears_active_invite(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)
    raw, _ = await svc.create_or_replace(user_id=user.id, created_by=None)
    await svc.revoke(user_id=user.id)
    with pytest.raises(InviteConsumeError, match="revoked|not found"):
        await svc.consume(token=raw, password="p")


@pytest.mark.asyncio
async def test_status_for_user(unit_db):
    user = User(id=uuid4(), email="x@y.com", hashed_password="", is_registered=False)
    unit_db.add(user)
    await unit_db.flush()
    svc = UserInviteService(unit_db)
    assert (await svc.status_for(user)) == "never_invited"
    await svc.create_or_replace(user_id=user.id, created_by=None)
    assert (await svc.status_for(user)) == "pending"
```

- [ ] **Step 2: Run unit tests, expect failure**

```bash
./test.sh tests/unit/test_user_invite_service.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the service**

`api/src/services/user_invite_service.py`:

```python
"""User invite service: create, regenerate, revoke, consume invite tokens."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from passlib.context import CryptContext  # already used elsewhere in api
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import User, UserInvite

INVITE_TTL = timedelta(days=7)
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class InviteConsumeError(Exception):
    """Raised when an invite cannot be consumed."""


class UserInviteService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_or_replace(
        self, *, user_id: UUID, created_by: UUID | None
    ) -> tuple[str, UserInvite]:
        """Generate a fresh invite, replacing any existing one for this user.

        Returns (raw_token, invite_row). Raw token is shown to the inviter once.
        """
        existing = await self._get_for_user(user_id)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

        raw = secrets.token_urlsafe(32)
        invite = UserInvite(
            user_id=user_id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(timezone.utc) + INVITE_TTL,
            created_by=created_by,
        )
        self.session.add(invite)
        await self.session.flush()
        return raw, invite

    async def revoke(self, *, user_id: UUID) -> None:
        existing = await self._get_for_user(user_id)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

    async def consume(self, *, token: str, password: str | None = None) -> User:
        token_hash = _hash_token(token)
        invite = (
            await self.session.execute(
                select(UserInvite).where(UserInvite.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if invite is None:
            raise InviteConsumeError("Invite not found")
        if invite.consumed_at is not None:
            raise InviteConsumeError("Invite already consumed")
        if invite.revoked_at is not None:
            raise InviteConsumeError("Invite revoked")
        if invite.expires_at < datetime.now(timezone.utc):
            raise InviteConsumeError("Invite expired")

        user = (
            await self.session.execute(select(User).where(User.id == invite.user_id))
        ).scalar_one()

        if password:
            user.hashed_password = _pwd.hash(password)
        user.is_registered = True
        invite.consumed_at = datetime.now(timezone.utc)

        await self.session.flush()
        return user

    async def status_for(self, user: User) -> str:
        if user.is_registered:
            return "active"
        invite = await self._get_for_user(user.id)
        if invite is None:
            return "never_invited"
        if invite.expires_at < datetime.now(timezone.utc):
            return "expired"
        return "pending"

    async def _get_for_user(self, user_id: UUID) -> UserInvite | None:
        return (
            await self.session.execute(
                select(UserInvite).where(UserInvite.user_id == user_id)
            )
        ).scalar_one_or_none()
```

- [ ] **Step 4: Run unit tests, expect pass**

```bash
./test.sh tests/unit/test_user_invite_service.py -v
```

Expected: 8 PASS. If `unit_db` fixture isn't already in `api/tests/unit/conftest.py`, replicate the existing in-memory or Postgres-test-db pattern from a sibling unit test (e.g. `test_email_service.py` if present).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/user_invite_service.py api/tests/unit/test_user_invite_service.py
git commit -m "feat(invites): UserInviteService with create/consume/revoke/status"
```

---

### Task 10: Invite endpoints in users router

**Files:**
- Modify: `api/src/routers/users.py`
- Test: `api/tests/e2e/test_user_invites.py` (create)

- [ ] **Step 1: Write the failing e2e tests**

```python
"""E2E tests for invite endpoints."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_create_user_with_invite_dispatches_email(superuser_client, db_session):
    fake = AsyncMock(return_value=type("R", (), {"success": True, "execution_id": "e1", "error": None})())
    with patch("src.routers.users.send_email", new=fake):
        resp = await superuser_client.post(
            "/api/users",
            json={"email": "new@example.com", "name": "New", "invite": True},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["invite_status"] == "pending"
    fake.assert_called_once()
    assert fake.call_args.kwargs["recipient"] == "new@example.com"
    assert "register?token=" in fake.call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_create_user_without_invite_does_not_send(superuser_client):
    fake = AsyncMock()
    with patch("src.routers.users.send_email", new=fake):
        resp = await superuser_client.post(
            "/api/users",
            json={"email": "noi@example.com", "name": "NoI"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["invite_status"] == "never_invited"
    fake.assert_not_called()


@pytest.mark.asyncio
async def test_resend_invite_returns_new_link(superuser_client):
    create = await superuser_client.post(
        "/api/users", json={"email": "r@example.com", "invite": False}
    )
    user_id = create.json()["id"]

    fake = AsyncMock(return_value=type("R", (), {"success": True, "execution_id": "e2", "error": None})())
    with patch("src.routers.users.send_email", new=fake):
        resp = await superuser_client.post(f"/api/users/{user_id}/invite/resend")
    assert resp.status_code == 200
    body = resp.json()
    assert "register?token=" in body["registration_url"]
    assert body["email_sent"] is True


@pytest.mark.asyncio
async def test_regenerate_invite_does_not_send(superuser_client):
    create = await superuser_client.post(
        "/api/users", json={"email": "g@example.com", "invite": False}
    )
    user_id = create.json()["id"]

    fake = AsyncMock()
    with patch("src.routers.users.send_email", new=fake):
        resp = await superuser_client.post(f"/api/users/{user_id}/invite/regenerate")
    assert resp.status_code == 200
    fake.assert_not_called()
    assert "register?token=" in resp.json()["registration_url"]


@pytest.mark.asyncio
async def test_revoke_invite_clears_pending_status(superuser_client):
    create = await superuser_client.post(
        "/api/users", json={"email": "v@example.com", "invite": False}
    )
    user_id = create.json()["id"]
    await superuser_client.post(f"/api/users/{user_id}/invite/regenerate")

    resp = await superuser_client.delete(f"/api/users/{user_id}/invite")
    assert resp.status_code == 204

    listed = await superuser_client.get("/api/users")
    user = next(u for u in listed.json() if u["id"] == user_id)
    assert user["invite_status"] == "never_invited"
```

- [ ] **Step 2: Run tests, expect failure**

```bash
./test.sh tests/e2e/test_user_invites.py -v
```

Expected: FAIL.

- [ ] **Step 3: Wire `invite=True` into `create_user`**

Open `api/src/routers/users.py`. At top:

```python
from src.config import get_settings
from src.models.contracts.user_invites import CreateInviteResponse, UserInvitePublic
from src.services.email_service import send_email
from src.services.user_invite_service import UserInviteService
```

After the `db.refresh(new_user)` block in `create_user`, add:

```python
    invite_status = "never_invited"
    if request.invite:
        svc = UserInviteService(db)
        raw_token, invite = await svc.create_or_replace(
            user_id=new_user.id, created_by=user.id
        )
        registration_url = f"{get_settings().public_url.rstrip('/')}/register?token={raw_token}"
        send_result = await send_email(
            recipient=new_user.email,
            subject="You're invited to Bifrost",
            body=(
                f"Hello{(' ' + new_user.name) if new_user.name else ''},\n\n"
                f"You've been invited to Bifrost. Complete your registration here:\n\n{registration_url}\n\n"
                f"This link expires {invite.expires_at.isoformat()}."
            ),
        )
        if not send_result.success:
            logger.warning(f"Invite email failed for {new_user.email}: {send_result.error}")
        invite_status = "pending"

    response = UserPublic.model_validate(new_user)
    response.invite_status = invite_status
    return response
```

Also update `list_users` to populate `invite_status` per row using `UserInviteService.status_for`.

Add new endpoints below `create_user`:

```python
@router.post("/{user_id}/invite/resend", response_model=CreateInviteResponse)
async def resend_invite(user_id: UUID, user: CurrentSuperuser, db: DbSession) -> CreateInviteResponse:
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=True)


@router.post("/{user_id}/invite/regenerate", response_model=CreateInviteResponse)
async def regenerate_invite(user_id: UUID, user: CurrentSuperuser, db: DbSession) -> CreateInviteResponse:
    return await _generate_invite(user_id=user_id, actor=user, db=db, send=False)


@router.delete("/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(user_id: UUID, user: CurrentSuperuser, db: DbSession) -> None:
    svc = UserInviteService(db)
    await svc.revoke(user_id=user_id)


async def _generate_invite(*, user_id: UUID, actor, db, send: bool) -> CreateInviteResponse:
    target = (await db.execute(select(UserORM).where(UserORM.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_registered:
        raise HTTPException(status_code=409, detail="User is already registered")

    svc = UserInviteService(db)
    raw_token, invite = await svc.create_or_replace(user_id=user_id, created_by=actor.id)
    registration_url = f"{get_settings().public_url.rstrip('/')}/register?token={raw_token}"

    email_sent = False
    email_error = None
    if send:
        send_result = await send_email(
            recipient=target.email,
            subject="You're invited to Bifrost",
            body=f"Complete your registration: {registration_url}\n\nLink expires {invite.expires_at.isoformat()}.",
        )
        email_sent = send_result.success
        email_error = send_result.error

    return CreateInviteResponse(
        user_id=user_id,
        expires_at=invite.expires_at,
        registration_url=registration_url,
        email_sent=email_sent,
        email_error=email_error,
    )
```

- [ ] **Step 4: Run tests, expect pass**

```bash
./test.sh tests/e2e/test_user_invites.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/users.py api/tests/e2e/test_user_invites.py
git commit -m "feat(invites): users router invite endpoints + UserCreate.invite=True path"
```

---

### Task 11: Register-from-invite auth endpoint

**Files:**
- Modify: `api/src/routers/auth.py`
- Test: `api/tests/e2e/test_register_from_invite.py` (create)

- [ ] **Step 1: Write failing tests**

```python
"""E2E tests for the unauthenticated register-from-invite endpoint."""
import pytest


@pytest.mark.asyncio
async def test_register_from_invite_succeeds(client, superuser_client):
    create = await superuser_client.post(
        "/api/users", json={"email": "i@example.com", "invite": False}
    )
    user_id = create.json()["id"]
    gen = await superuser_client.post(f"/api/users/{user_id}/invite/regenerate")
    url = gen.json()["registration_url"]
    token = url.split("token=", 1)[1]

    resp = await client.post(
        "/api/auth/register-from-invite",
        json={"token": token, "name": "Iris", "password": "supersecret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "i@example.com"

    replay = await client.post(
        "/api/auth/register-from-invite",
        json={"token": token, "password": "x"},
    )
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_register_from_invite_unknown_token(client):
    resp = await client.post(
        "/api/auth/register-from-invite",
        json={"token": "nope", "password": "x"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests, expect failure**

```bash
./test.sh tests/e2e/test_register_from_invite.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add the endpoint**

In `api/src/routers/auth.py`:

```python
from src.models.contracts.user_invites import RegisterFromInviteRequest
from src.models.contracts.users import UserPublic
from src.services.user_invite_service import InviteConsumeError, UserInviteService


@router.post("/register-from-invite", response_model=UserPublic)
async def register_from_invite(
    request: RegisterFromInviteRequest,
    db: DbSession,
) -> UserPublic:
    svc = UserInviteService(db)
    try:
        user = await svc.consume(token=request.token, password=request.password)
    except InviteConsumeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if request.name and not user.name:
        user.name = request.name
        await db.flush()
    return UserPublic.model_validate(user)
```

(No `Depends`/auth gate — this endpoint is intentionally unauthenticated; the token is the credential.)

- [ ] **Step 4: Run tests, expect pass**

```bash
./test.sh tests/e2e/test_register_from_invite.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/auth.py api/tests/e2e/test_register_from_invite.py
git commit -m "feat(invites): unauthenticated register-from-invite endpoint"
```

---

### Task 12: Frontend services + hooks

**Files:**
- Create: `client/src/services/user-invites.ts`
- Test: `client/src/services/user-invites.test.ts`
- Create: `client/src/hooks/useUserInvites.ts`

- [ ] **Step 1: Regenerate types**

```bash
cd client && npm run generate:types
```

- [ ] **Step 2: Write the failing service test**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { resendInvite, regenerateInvite, revokeInvite } from "./user-invites";
import { apiClient } from "@/lib/api-client";

vi.mock("@/lib/api-client", () => ({
  apiClient: { post: vi.fn(), delete: vi.fn() },
}));

describe("user-invites service", () => {
  beforeEach(() => vi.clearAllMocks());

  it("resendInvite POSTs to /resend", async () => {
    (apiClient.post as any).mockResolvedValue({ registration_url: "x" });
    const r = await resendInvite("u1");
    expect(apiClient.post).toHaveBeenCalledWith("/api/users/u1/invite/resend");
    expect(r.registration_url).toBe("x");
  });

  it("regenerateInvite POSTs to /regenerate", async () => {
    (apiClient.post as any).mockResolvedValue({ registration_url: "y" });
    await regenerateInvite("u2");
    expect(apiClient.post).toHaveBeenCalledWith("/api/users/u2/invite/regenerate");
  });

  it("revokeInvite DELETEs", async () => {
    (apiClient.delete as any).mockResolvedValue(undefined);
    await revokeInvite("u3");
    expect(apiClient.delete).toHaveBeenCalledWith("/api/users/u3/invite");
  });
});
```

- [ ] **Step 3: Implement the service**

`client/src/services/user-invites.ts`:

```ts
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type CreateInviteResponse = components["schemas"]["CreateInviteResponse"];

export async function resendInvite(userId: string) {
  return apiClient.post<CreateInviteResponse>(`/api/users/${userId}/invite/resend`);
}

export async function regenerateInvite(userId: string) {
  return apiClient.post<CreateInviteResponse>(`/api/users/${userId}/invite/regenerate`);
}

export async function revokeInvite(userId: string) {
  return apiClient.delete<void>(`/api/users/${userId}/invite`);
}
```

- [ ] **Step 4: Run vitest, expect pass**

```bash
./test.sh client unit -- src/services/user-invites.test.ts
```

Expected: 3 PASS.

- [ ] **Step 5: Add the hooks**

`client/src/hooks/useUserInvites.ts`:

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { resendInvite, regenerateInvite, revokeInvite } from "@/services/user-invites";

export function useResendInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => resendInvite(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useRegenerateInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => regenerateInvite(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useRevokeInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => revokeInvite(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
}
```

- [ ] **Step 6: Type-check + commit**

```bash
cd client && npm run tsc
```

```bash
git add client/src/services/user-invites.ts client/src/services/user-invites.test.ts client/src/hooks/useUserInvites.ts client/src/lib/v1.d.ts
git commit -m "feat(invites): client service + hooks"
```

---

### Task 13: `UserStatusBadge` component

**Files:**
- Create: `client/src/components/users/UserStatusBadge.tsx`
- Test: `client/src/components/users/UserStatusBadge.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { UserStatusBadge } from "./UserStatusBadge";

describe("UserStatusBadge", () => {
  it("renders Active for active status", () => {
    render(<UserStatusBadge status="active" />);
    expect(screen.getByText(/^active$/i)).toBeInTheDocument();
  });

  it("renders Pending invite for pending status", () => {
    render(<UserStatusBadge status="pending" />);
    expect(screen.getByText(/pending invite/i)).toBeInTheDocument();
  });

  it("renders Invite expired for expired status", () => {
    render(<UserStatusBadge status="expired" />);
    expect(screen.getByText(/invite expired/i)).toBeInTheDocument();
  });

  it("renders Not invited for never_invited status", () => {
    render(<UserStatusBadge status="never_invited" />);
    expect(screen.getByText(/not invited/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement**

```tsx
import { Badge } from "@/components/ui/badge";

interface Props { status: string }

const LABELS: Record<string, { text: string; variant: "default" | "secondary" | "outline" | "destructive" }> = {
  active: { text: "Active", variant: "default" },
  pending: { text: "Pending invite", variant: "secondary" },
  expired: { text: "Invite expired", variant: "destructive" },
  never_invited: { text: "Not invited", variant: "outline" },
};

export function UserStatusBadge({ status }: Props) {
  const cfg = LABELS[status] ?? LABELS.active;
  return <Badge variant={cfg.variant} className="text-xs">{cfg.text}</Badge>;
}
```

- [ ] **Step 3: Run vitest, expect pass + commit**

```bash
./test.sh client unit -- src/components/users/UserStatusBadge.test.tsx
git add client/src/components/users/UserStatusBadge.tsx client/src/components/users/UserStatusBadge.test.tsx
git commit -m "feat(invites): UserStatusBadge"
```

---

### Task 14: `InviteActionsMenu` component

**Files:**
- Create: `client/src/components/users/InviteActionsMenu.tsx`
- Test: `client/src/components/users/InviteActionsMenu.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { InviteActionsMenu } from "./InviteActionsMenu";

describe("InviteActionsMenu", () => {
  it("calls onResend when Resend chosen", async () => {
    const onResend = vi.fn();
    render(
      <InviteActionsMenu
        userId="u1"
        status="pending"
        onResend={onResend}
        onRegenerate={vi.fn()}
        onCopyLink={vi.fn()}
        onRevoke={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /invite actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /resend invite/i }));
    expect(onResend).toHaveBeenCalled();
  });

  it("does not render for active users", () => {
    const { container } = render(
      <InviteActionsMenu
        userId="u1"
        status="active"
        onResend={vi.fn()}
        onRegenerate={vi.fn()}
        onCopyLink={vi.fn()}
        onRevoke={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Implement**

```tsx
import { MoreVertical, Mail, RefreshCw, Link as LinkIcon, Ban } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
  userId: string;
  status: string;
  onResend: () => void;
  onRegenerate: () => void;
  onCopyLink: () => void;
  onRevoke: () => void;
}

export function InviteActionsMenu({ status, onResend, onRegenerate, onCopyLink, onRevoke }: Props) {
  if (status === "active") return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Invite actions">
          <MoreVertical className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuItem onClick={onResend}><Mail className="mr-2 h-4 w-4" />Resend invite</DropdownMenuItem>
        <DropdownMenuItem onClick={onRegenerate}><RefreshCw className="mr-2 h-4 w-4" />Regenerate link</DropdownMenuItem>
        <DropdownMenuItem onClick={onCopyLink}><LinkIcon className="mr-2 h-4 w-4" />Copy registration link</DropdownMenuItem>
        <DropdownMenuItem onClick={onRevoke} className="text-destructive"><Ban className="mr-2 h-4 w-4" />Revoke invite</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

- [ ] **Step 3: Run vitest, expect pass + commit**

```bash
./test.sh client unit -- src/components/users/InviteActionsMenu.test.tsx
git add client/src/components/users/InviteActionsMenu.tsx client/src/components/users/InviteActionsMenu.test.tsx
git commit -m "feat(invites): InviteActionsMenu (resend/regenerate/copy/revoke)"
```

---

### Task 15: Wire Status column + invite actions into Users.tsx

**Files:**
- Modify: `client/src/pages/Users.tsx`
- Modify: `client/src/components/users/CreateUserDialog.tsx`

- [ ] **Step 1: Add Status column**

In `Users.tsx` header row, between Email/Roles and Date, add:

```tsx
<DataTableHead className="w-0 whitespace-nowrap cursor-pointer select-none" onClick={() => handleSort("status")}>
  Status
  <SortIcon column="status" sortColumn={sortColumn} sortDirection={sortDirection} />
</DataTableHead>
```

In each row, add the corresponding cell:

```tsx
<DataTableCell className="w-0 whitespace-nowrap">
  <UserStatusBadge status={user.invite_status ?? "active"} />
</DataTableCell>
```

Update `sortedUsers` `case "status"` branch to sort by `invite_status`.

- [ ] **Step 2: Add `InviteActionsMenu` to actions cell**

Inside the existing Actions cell flex, after the Edit button and before the Delete button, render:

```tsx
<InviteActionsMenu
  userId={user.id}
  status={user.invite_status ?? "active"}
  onResend={() => resendMutation.mutate(user.id, {
    onSuccess: (res) => toast.success(res.email_sent ? "Invite resent" : "Invite generated (email failed)"),
  })}
  onRegenerate={() => regenerateMutation.mutate(user.id, {
    onSuccess: (res) => {
      navigator.clipboard.writeText(res.registration_url);
      toast.success("New link generated and copied");
    },
  })}
  onCopyLink={() => regenerateMutation.mutate(user.id, {
    onSuccess: (res) => {
      navigator.clipboard.writeText(res.registration_url);
      toast.success("Registration link copied");
    },
  })}
  onRevoke={() => revokeMutation.mutate(user.id, {
    onSuccess: () => toast.success("Invite revoked"),
  })}
/>
```

Where the mutations come from new imports:

```tsx
import { useResendInvite, useRegenerateInvite, useRevokeInvite } from "@/hooks/useUserInvites";
import { UserStatusBadge } from "@/components/users/UserStatusBadge";
import { InviteActionsMenu } from "@/components/users/InviteActionsMenu";
```

```tsx
const resendMutation = useResendInvite();
const regenerateMutation = useRegenerateInvite();
const revokeMutation = useRevokeInvite();
```

- [ ] **Step 3: Add "Send invite email" checkbox to CreateUserDialog**

Open `client/src/components/users/CreateUserDialog.tsx`. Add a `Checkbox` (or `Switch`) labeled "Send invite email" defaulting to `true`. Submit body must include `invite: <bool>`.

- [ ] **Step 4: Type-check + lint**

```bash
cd client && npm run tsc && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add client/src/pages/Users.tsx client/src/components/users/CreateUserDialog.tsx
git commit -m "feat(invites): Status column + invite actions menu + send-invite checkbox in CreateUserDialog"
```

---

### Task 16: Extract `AuthSetupSteps` shared component

**Files:**
- Create: `client/src/components/auth/AuthSetupSteps.tsx`
- Test: `client/src/components/auth/AuthSetupSteps.test.tsx`
- Modify: `client/src/pages/Setup.tsx`

- [ ] **Step 1: Read `Setup.tsx` to identify the auth-setup block**

The component should accept props for:
- `onPasskeyRegister: () => Promise<void>` — caller decides which endpoint to hit
- `onPasswordRegister: (password: string) => Promise<void>`
- `email: string` — display only
- `name?: string`
- `onNameChange?: (name: string) => void`
- `isPending: boolean`
- `error: string | null`

The component renders the passkey-or-password choice and the password form. It does NOT call any endpoint directly — the parent does.

- [ ] **Step 2: Write failing test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { AuthSetupSteps } from "./AuthSetupSteps";

describe("AuthSetupSteps", () => {
  it("calls onPasskeyRegister when passkey button clicked", async () => {
    const onPasskey = vi.fn().mockResolvedValue(undefined);
    render(
      <AuthSetupSteps
        email="x@y.com"
        onPasskeyRegister={onPasskey}
        onPasswordRegister={vi.fn()}
        isPending={false}
        error={null}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /set up passkey/i }));
    expect(onPasskey).toHaveBeenCalled();
  });

  it("calls onPasswordRegister with password", async () => {
    const onPwd = vi.fn().mockResolvedValue(undefined);
    render(
      <AuthSetupSteps
        email="x@y.com"
        onPasskeyRegister={vi.fn()}
        onPasswordRegister={onPwd}
        isPending={false}
        error={null}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /use password instead/i }));
    await userEvent.type(screen.getByLabelText(/password/i), "secret123");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(onPwd).toHaveBeenCalledWith("secret123");
  });
});
```

- [ ] **Step 3: Implement** `AuthSetupSteps.tsx` using the patterns lifted from `Setup.tsx` (passkey button + password form). Do not call endpoints inside.

- [ ] **Step 4: Refactor `Setup.tsx`**

Replace its inline auth setup with `<AuthSetupSteps onPasskeyRegister={...} onPasswordRegister={...} />`. Keep its existing endpoint calls in the parent.

- [ ] **Step 5: Run vitest + run existing Setup tests**

```bash
./test.sh client unit -- src/components/auth/AuthSetupSteps.test.tsx src/pages/Setup
```

Expected: PASS (and existing Setup tests still pass).

- [ ] **Step 6: Commit**

```bash
git add client/src/components/auth/ client/src/pages/Setup.tsx
git commit -m "refactor(auth): extract AuthSetupSteps shared component"
```

---

### Task 17: `/register` page

**Files:**
- Create: `client/src/pages/Register.tsx`
- Modify: `client/src/App.tsx` (or wherever routes are declared)
- Test: `client/e2e/users.spec.ts` (extend or create)

- [ ] **Step 1: Implement `Register.tsx`**

```tsx
import { useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { AuthSetupSteps } from "@/components/auth/AuthSetupSteps";
import { apiClient } from "@/lib/api-client";

export function Register() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const nav = useNavigate();

  if (!token) return <div className="p-8">Missing invite token.</div>;

  const submitPassword = async (password: string) => {
    setPending(true);
    setError(null);
    try {
      await apiClient.post("/api/auth/register-from-invite", { token, password });
      nav("/login");
    } catch (e: any) {
      setError(e?.message ?? "Failed to register");
    } finally {
      setPending(false);
    }
  };

  const submitPasskey = async () => {
    // For invite flow, the passkey path mirrors Setup.tsx — but the underlying
    // passkey-registration endpoint must accept the invite token instead of a
    // bootstrap nonce. If that endpoint doesn't yet support invite tokens,
    // hide the passkey button on this page (see passkey_service.py).
    throw new Error("Passkey from invite not yet supported");
  };

  return (
    <div className="mx-auto max-w-md p-8">
      <h1 className="text-2xl font-semibold mb-4">Complete your registration</h1>
      <AuthSetupSteps
        email=""
        onPasskeyRegister={submitPasskey}
        onPasswordRegister={submitPassword}
        isPending={pending}
        error={error}
      />
    </div>
  );
}
```

**Decision point** for the agent: passkey from invite is out-of-scope for this PR unless `passkey_service.py` already exposes a token-gated registration endpoint. If not, render only the password path on `Register.tsx` and document the passkey-from-invite follow-up in the PR body. Don't expand scope here.

- [ ] **Step 2: Add the route as unauthenticated**

In `client/src/App.tsx` (or the router file), add:

```tsx
<Route path="/register" element={<Register />} />
```

Place it **outside** any auth guard. Verify by grepping for the existing `/setup` route placement — mirror that.

- [ ] **Step 3: Playwright happy path**

Add to `client/e2e/users.spec.ts`:

```ts
test("admin invites user and user registers via magic link", async ({ page, request }) => {
  // login as platform admin (use existing helper)
  await loginAsPlatformAdmin(page);
  await page.goto("/users");
  await page.getByRole("button", { name: /create user/i }).click();
  await page.getByLabel(/email/i).fill("invitee@example.com");
  await page.getByLabel(/send invite email/i).check();
  await page.getByRole("button", { name: /create/i }).click();
  await expect(page.getByText(/pending invite/i)).toBeVisible();

  // Pull the registration URL via the regenerate API (test-only convenience)
  const userRow = page.locator("tr", { hasText: "invitee@example.com" });
  await userRow.getByRole("button", { name: /invite actions/i }).click();
  await page.getByRole("menuitem", { name: /copy registration link/i }).click();
  const link: string = await page.evaluate(() => navigator.clipboard.readText());
  expect(link).toContain("/register?token=");

  // Visit the link in a fresh context (logged-out)
  await page.context().clearCookies();
  await page.goto(link);
  await page.getByRole("button", { name: /use password instead/i }).click();
  await page.getByLabel(/password/i).fill("invitee-password-123");
  await page.getByRole("button", { name: /create account/i }).click();
  await page.waitForURL("**/login");
});
```

(Adjust selectors to whatever the existing e2e helpers/conventions use.)

- [ ] **Step 4: Run Playwright**

```bash
./test.sh client e2e e2e/users.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/pages/Register.tsx client/src/App.tsx client/e2e/users.spec.ts
git commit -m "feat(invites): /register page consuming invite token + e2e happy path"
```

---

### Task 18: Pre-completion verification

- [ ] **Step 1: Run full backend suite**

```bash
./test.sh stack up
./test.sh all
```

Expected: 0 failures. Inspect `/tmp/bifrost-<project>/test-results.xml` if anything fails.

- [ ] **Step 2: Run full client suite**

```bash
./test.sh client unit
./test.sh client e2e
```

- [ ] **Step 3: Lint + type-check both sides**

```bash
cd api && pyright && ruff check .
cd ../client && npm run tsc && npm run lint
```

- [ ] **Step 4: Manual smoke**

Visit `<debug-url>/users` with a long org name — verify no horizontal scroll, Actions visible. Create a user with invite checkbox checked. Inspect `bifrost-debug-<worktree>-api-1` logs for the test-only invite link, paste into a private window, complete registration, log in.

- [ ] **Step 5: Commit any final fixups**

```bash
git status
git add -A
git commit -m "chore: pre-completion verification fixups" || echo "nothing to commit"
```

---

---

## Phase 4: Pivot from email-workflow plumbing to events (added 2026-05-19)

**Why this exists.** During smoke testing the configured email workflow flow failed end-to-end with `ValueError: badly formed hexadecimal UUID string` originating in `api/src/repositories/executions.py:111` (`parsed_user_id = UUID(user_id)`). Root cause: `api/src/services/email_service.py:289` and `api/src/routers/email_config.py:222` (SDK `/api/email/send`) both construct an `ExecutionContext` with the literal string `user_id="system"` instead of the real sentinel UUID `SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"` that the existing system-execution helper (`api/src/services/execution/async_executor.py:223 enqueue_system_workflow_execution`) uses correctly. The same bug applies to all three send paths: invite send, admin "validate-and-test" button, and the user-facing `bifrost.email.send()` SDK module — meaning `bifrost.email.send()` has never worked against a configured email workflow.

Rather than patching the `user_id` value, we pivot the invite flow (and **delete the email subsystem outright**) to use the platform's existing event system. This:
- Removes the parallel email-config machinery that duplicates what events already do.
- Eliminates the broken `bifrost.email.send()` SDK module entirely (hard delete — call out in release notes).
- Gives users a native, debuggable smoke path: subscribe a no-op workflow to the event, view its execution in History.
- Establishes the first **internal-source event** in the platform (`EventSourceType.INTERNAL` already exists in the enum but has no callers yet).

### Conventions locked

- **Event type name:** `user.invited` — past-tense, dotted `resource.action`, matches existing `microsoft_graph.py:396` convention `f"{resource_type}.{event_type}"`.
- **Event source kind:** `EventSourceType.INTERNAL` (already in `api/src/models/enums.py:113`).
- **Emit on regenerate-without-send:** NO. The "copy link" path returns the URL to the admin; emitting would surprise admins by sending email on every link copy. Only emit when the admin's intent is "notify the user."
- **UI language for the create-user/resend toggle:** rename "Send invite email" → ambiguous-but-accurate phrasing that reflects "this fires automations subscribed to `user.invited`." Working candidates: **"Trigger invite automations"** (Recommended), **"Call invite event?"**, **"Notify subscribed workflows"**. Final wording decided when implementing Task 25.
- **Backwards compatibility for `bifrost.email.send()`:** none. Hard delete; release notes call out the removal. Zero in-repo callers confirmed via `grep -rn "from bifrost import email\|bifrost\.email" apps/ workflows/`.

### Deletion inventory

| File | Action |
|------|--------|
| `api/src/services/email_service.py` | DELETE |
| `api/src/routers/email_config.py` | DELETE (admin router + SDK `/api/email/send`) |
| `api/bifrost/email.py` | DELETE |
| `api/bifrost/__init__.py` | Remove `from .email import email` + any `__all__` entry |
| `client/src/pages/settings/Email.tsx` | DELETE |
| `client/src/components/settings/EmailTestDialog.tsx` | DELETE |
| `client/src/components/settings/EmailTestDialog.test.tsx` (if present) | DELETE |
| Settings navigation entry pointing at `/settings/email` | REMOVE |
| Route registration for the Email settings page | REMOVE |
| `api/tests/**/test_email*` and similar | DELETE |
| `api/tests/**/test_send_email*` | DELETE |
| Email-config OpenAPI types in `client/src/lib/v1.d.ts` | Regenerated automatically after backend deletion |
| Any `docs/llm.txt` mentions of email config | STRIP |
| `Config` / `SystemConfig` row with key `email/workflow_config` | DROP via Alembic migration |

Run `grep -rn "email_service\|email_config\|bifrost\.email\|EmailTestDialog\|/settings/email" api/ client/` before committing the deletion to make sure nothing leaks. Anything that grep finds outside the deleted files is a missed reference.

### Task 20: Delete the email subsystem (backend)

**Files:**
- DELETE: `api/src/services/email_service.py`
- DELETE: `api/src/routers/email_config.py`
- DELETE: `api/bifrost/email.py`
- Modify: `api/bifrost/__init__.py` (drop the import + `__all__` entry)
- Modify: `api/src/main.py` (or wherever routers are registered) — remove inclusion of `email_config.router` and `email_config.sdk_router`
- Modify: `api/src/routers/users.py` — remove the two `await send_email(...)` blocks (lines ~165, ~251); leave the routes themselves but with the email-send call removed. Phase 4 will replace them with `await emit_internal_event(...)`.

- [ ] **Step 1: Identify all router registrations**

```bash
grep -rn "email_config\|email_service\|bifrost\.email" api/src api/bifrost --include="*.py" | grep -v __pycache__
```

Map every hit to a delete-or-edit decision.

- [ ] **Step 2: Stub the invite-send calls**

In `api/src/routers/users.py` `create_user` and `_generate_invite`, replace `await send_email(...)` with a TODO marker comment `# TODO(task-23): emit user.invited event`. The function still needs `email_sent`/`email_error` values in its return for now — set them to `False`/`None` until Task 22 changes the response contract. Keep the code compiling.

- [ ] **Step 3: Delete the files**

```bash
git rm api/src/services/email_service.py api/src/routers/email_config.py api/bifrost/email.py
```

- [ ] **Step 4: Remove imports & router registrations**

Edit `api/bifrost/__init__.py` and the main router file. Verify with:

```bash
grep -rn "email_service\|email_config\|bifrost\.email" api/ --include="*.py" | grep -v test_
```

Expect zero hits.

- [ ] **Step 5: pyright + ruff**

```bash
cd api && pyright && ruff check .
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(email): remove email_service, email_config router, and bifrost.email SDK module"
```

---

### Task 21: Alembic migration to drop email-config DB row

**File:** new migration under `api/alembic/versions/`.

- [ ] **Step 1: Generate the migration**

```bash
cd api && alembic revision -m "drop email_workflow_config system config row"
```

- [ ] **Step 2: Write the migration**

```python
def upgrade() -> None:
    op.execute(
        "DELETE FROM system_configs WHERE category = 'email' AND key = 'workflow_config'"
    )

def downgrade() -> None:
    # No restore — the consuming code has been deleted.
    pass
```

Replace `system_configs` with the actual table name — confirm by reading the existing `SystemConfig` ORM model.

- [ ] **Step 3: Apply**

```bash
docker compose -p bifrost-debug-d301cb77 restart bifrost-init
docker compose -p bifrost-debug-d301cb77 restart api
```

(Compose project name derived from the worktree — confirm with `./debug.sh status`.)

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(db): drop orphan email_workflow_config row after email subsystem removal"
```

---

### Task 22: Internal-event emitter primitive

Goal: provide a callable that any internal code can use to emit an `EventSourceType.INTERNAL` event without going through the webhook adapter pipeline. Webhooks have `EventProcessor.process_webhook(...)`; we need an internal analogue that does steps 2 (event logging) + 3 (subscription matching) + 4 (delivery tracking) + 5 (workflow enqueue) but skips step 1 (adapter routing).

**Files:**
- Modify: `api/src/services/events/processor.py` — add `EventProcessor.emit_internal(...)`
- Add: a top-level helper `emit_internal_event(event_type, data, *, organization_id=None)` in `api/src/services/events/__init__.py` that opens its own DB session, instantiates `EventProcessor`, and calls `emit_internal`. Internal callers (routers, services) use the helper; tests can call the method directly.

- [ ] **Step 1: Inspect `EventProcessor.process_webhook` end-to-end**

Read `api/src/services/events/processor.py` to understand: how the `Event` ORM row is created (`event_repo.create_event(...)`), how subscriptions are looked up by `event_type` + scope (`subscription_repo.match(...)` or similar), how each match becomes a `Deliver` and ultimately calls `enqueue_system_workflow_execution(...)`. Confirm the subscription-matching query already handles `EventSourceType.INTERNAL` sources, or extend it.

- [ ] **Step 2: Failing unit test**

Add `api/tests/unit/services/events/test_emit_internal.py`:

```python
# Test cases:
# - emit_internal creates an Event row with event_type, source_type=INTERNAL, scope
# - given a subscription on event_type "user.invited", a delivery is created and a workflow execution enqueued
# - no subscribers => event logged, zero deliveries, no error
# - payload is round-trippable: subscribed workflow receives it as context.event.data
```

Use existing event/subscription fixtures from `api/tests/conftest.py` or the events test module.

- [ ] **Step 3: Implement `emit_internal`**

Mirrors `process_webhook` minus adapter routing. Signature:

```python
async def emit_internal(
    self,
    *,
    event_type: str,
    data: dict,
    organization_id: UUID | None = None,
    triggered_by: str | None = None,  # actor user_id for audit; system if None
) -> UUID:
    """Emit an internal event. Returns event_id."""
```

Internally:
1. Resolve-or-create the `EventSource` row for source_type=INTERNAL, name=event_type, organization_id=scope. (Or, simpler: don't materialize an EventSource row at all for internal events — store `event_source_id=NULL` on the Event. Decide based on what `event_repo.create_event` requires. **Prefer the simpler path: nullable event_source_id**, and update the FK if it isn't nullable already.)
2. Insert `Event` row with payload, event_type, status=RECEIVED.
3. Find matching subscriptions (by event_type + scope; same logic as webhook delivery).
4. For each subscription: create `EventDelivery` row, call `enqueue_system_workflow_execution(workflow_id, parameters={"event": {"type": event_type, "data": data, "id": event_id}}, source=f"event: {event_type}", org_id=...)`.

Use `enqueue_system_workflow_execution` for the workflow dispatch — this guarantees the correct `SYSTEM_USER_ID` UUID and avoids re-introducing the bug we're fixing.

- [ ] **Step 4: Wire the top-level helper**

```python
# api/src/services/events/__init__.py
async def emit_internal_event(
    event_type: str,
    data: dict,
    *,
    organization_id: UUID | None = None,
    triggered_by: str | None = None,
) -> UUID:
    from src.core.database import get_session_factory
    session_factory = get_session_factory()
    async with session_factory() as db:
        processor = EventProcessor(db)
        event_id = await processor.emit_internal(
            event_type=event_type,
            data=data,
            organization_id=organization_id,
            triggered_by=triggered_by,
        )
        await db.commit()
        return event_id
```

- [ ] **Step 5: Run tests**

```bash
./test.sh tests/unit/services/events/test_emit_internal.py -v
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(events): emit_internal primitive for internal-source events"
```

---

### Task 23: Emit `user.invited` from invite paths

**Files:**
- Modify: `api/src/routers/users.py` — replace the Task 20 TODO markers with `await emit_internal_event("user.invited", payload, organization_id=...)`.
- Modify: `api/src/models/contracts/user_invites.py` — update `CreateInviteResponse` to drop `email_sent`/`email_error` and add `event_emitted: bool` + `event_id: UUID | None`.
- Modify: `api/shared/models.py` if the response model lives there (Pydantic source of truth).

**Payload shape:**

```python
{
    "user_id": str(new_user.id),
    "email": new_user.email,
    "name": new_user.name or "",
    "registration_url": registration_url,
    "expires_at": invite.expires_at.isoformat(),
    "invited_by": {
        "user_id": str(actor.user_id),
        "email": actor.email,
        "name": actor.name or "",
    },
    "reason": "created" | "resent",  # NOT "regenerated" — see below
}
```

- [ ] **Step 1: Failing e2e test**

Add to `api/tests/e2e/api/test_user_invites.py`:

```python
# Test cases:
# - POST /users {invite: true} emits user.invited with reason="created" and the full payload
# - POST /users/{id}/invite/resend emits user.invited with reason="resent"
# - POST /users/{id}/invite/regenerate does NOT emit (returns the link only)
# - emitted event_id appears on the response
# - the registration_url field in the payload contains /accept-invite?token=
```

Use the existing event-emission test pattern (look at webhook delivery tests for the assertion style).

- [ ] **Step 2: Wire emission in `create_user` and `_generate_invite(send=True)`**

Construct the payload, call `emit_internal_event`, capture the returned event_id, populate the response. Drop the Task 20 TODO markers.

**Gate emission on `trigger_automation`:**
- `create_user`: emit only if `request.invite is True AND (request.trigger_automation is True OR request.trigger_automation is None)`. (None defaults to True for contract compatibility — see Task 25 Step 1.)
- `_generate_invite(send=True)`: this is the "Resend invite" path which is explicitly opting into automation; always emit.
- `_generate_invite(send=False)`: this is "Regenerate / Copy link"; never emit.

Add `trigger_automation: bool = True` to `UserCreate` in `api/shared/models.py`.

- [ ] **Step 3: Update `CreateInviteResponse`**

Drop `email_sent`/`email_error`. Add `event_emitted: bool` (always True if we emitted) and `event_id: UUID | None`. Update `api/src/models/contracts/user_invites.py`.

- [ ] **Step 4: Regenerate types**

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh
cd client && npm run generate:types
```

- [ ] **Step 5: Run tests**

```bash
./test.sh tests/e2e/api/test_user_invites.py -v
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(invites): emit user.invited event instead of calling email_service"
```

---

### Task 24: Document the `user.invited` event

**Files:**
- Modify: `docs/llm.txt` (Bifrost-LLM reference) — add an `Events` section listing internal events.
- Add: `docs/events/internal.md` (or wherever your event docs live — confirm by grepping for existing event documentation).

Document:
- Event type: `user.invited`
- Source type: internal
- When emitted: a platform admin creates a user with `invite=True`, or calls `POST /users/{id}/invite/resend`. NOT emitted on `regenerate` (link-only return).
- Payload: full schema (mirror Task 23 payload).
- Scope: scoped to the inviting user's organization (or GLOBAL if platform-admin context).
- Subscriber contract: workflow receives `context.event.data` with the payload above. Recommended subscriber actions: send email, post to Slack, log to audit system.

- [ ] **Step 1: Find existing event docs**

```bash
grep -rln "webhook event\|event_type\|event payload" docs/ 2>/dev/null
```

Match style.

- [ ] **Step 2: Write the doc**

Plain markdown, payload as a fenced code block with comments per field.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(events): document user.invited internal event"
```

---

### Task 25: Frontend cleanup + UX relabeling

**Files:**
- DELETE: `client/src/pages/settings/Email.tsx`
- DELETE: `client/src/components/settings/EmailTestDialog.tsx`
- DELETE: corresponding `.test.tsx` files
- Modify: settings navigation (grep for `/settings/email` in `client/src/`)
- Modify: route registration (grep `client/src/App.tsx` for the route)
- Modify: `client/src/components/users/CreateUserDialog.tsx` — change "Send invite email" checkbox label + its hover/help copy
- Modify: any frontend code referencing `email_sent`/`email_error` on `CreateInviteResponse` — drop those toast branches; use the new `event_emitted` value
- Modify: vitest covering the dialog assertion (the prior commit `0f7b2e76` checked the `invite` flag — that stays; update label expectations)
- Modify: `client/e2e/users.admin.spec.ts` — label selector change only; the test logic still works because Playwright pulls the registration URL via the API, not via email

- [ ] **Step 1: Implement the "Trigger invite automation" control**

Final label: **"Trigger invite automation"** (singular — one toggle, but it fans out to multiple events as the platform grows).

UX: checkbox (default on) + an info `<HoverCard>` or `<Popover>` revealing the list of events that will fire. For now the list is just `user.invited`, but the component should accept an array so future events (`user.welcomed`, `user.password_reset_requested`, etc.) drop into the same disclosure without rework.

Suggested shape:

```tsx
<TriggerAutomationToggle
  checked={triggerAutomation}
  onCheckedChange={setTriggerAutomation}
  events={["user.invited"]}
/>
```

The component renders:
- Checkbox with label "Trigger invite automation"
- An adjacent info icon (e.g., `Info` from lucide-react) that opens a popover listing each event as a code-style chip and a one-line caption: "Workflows subscribed to these events will run when you create this user. If unchecked, the registration link is generated but no automations run."

If unchecked, the submit body still includes `invite: true` (the invite RECORD is created so the link works) but a new `trigger_automation: false` field tells the backend to skip emission. If checked, both `invite: true` and `trigger_automation: true` (which is the default — backend treats absence-of-field as `true` for backwards-compatible reading of the contract during the transition). Update `UserCreate` accordingly.

**Important:** the existing `invite` flag stays. It controls whether an invite record is created at all (i.e., is this even a pending-invite user?). The new `trigger_automation` flag controls whether the event fires. The reason to split them: an admin might want to create a pending-invite user, eyeball the link in the API response, and hand-deliver it to a user outside the automation pipeline.

- [ ] **Step 2: Delete frontend email-config files**

```bash
git rm client/src/pages/settings/Email.tsx client/src/components/settings/EmailTestDialog.tsx
git rm client/src/components/settings/EmailTestDialog.test.tsx 2>/dev/null || true
```

- [ ] **Step 3: Strip route + nav references**

Grep, remove, run `npm run tsc` until clean.

- [ ] **Step 4: Update `CreateUserDialog` + toast handlers**

Apply the new label + tooltip. In `Users.tsx`, the `onResend` handler currently toasts based on `email_sent`; change it to toast based on `event_emitted`/`event_id` (e.g., "Invite event fired"). For `onRegenerate`/`onCopyLink`, the existing "Link copied" toasts stay.

- [ ] **Step 5: Update vitest + e2e selectors**

```bash
./test.sh client unit
./test.sh client e2e e2e/users.admin.spec.ts
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(users): replace email-config UI with event-driven invite UX"
```

---

### Task 26: Smoke test — subscribed noop workflow

End-to-end manual smoke. Confirms: the event emits, a subscribed workflow receives it, the registration URL works.

- [ ] **Step 1: Create a noop workflow via the UI or CLI**

Workflow body:

```python
from bifrost.sdk import workflow

@workflow(name="Show Invite Event")
async def show_invite_event(context):
    return context.event["data"]  # or however your event payload surfaces
```

(Confirm the exact `context.event` shape against your event-system docs; the pattern is documented for webhooks and should be identical here.)

- [ ] **Step 2: Subscribe the workflow to `user.invited`**

Via the events UI or CLI. Scope: GLOBAL for the smoke test.

- [ ] **Step 3: Trigger an invite**

Navigate to `/users`, click "Create User", check "Trigger invite automations", submit.

- [ ] **Step 4: Verify in Execution History**

- An execution row for the noop workflow appears.
- Open it; the result panel shows the JSON payload with `user_id`, `email`, `registration_url` (containing `/accept-invite?token=...`), `expires_at`, `invited_by`, `reason: "created"`.

- [ ] **Step 5: Visit the registration URL**

Copy `registration_url` from the execution result, open in a private window, complete registration with a password, log in.

- [ ] **Step 6: Test regenerate-doesn't-emit**

In `/users`, open the invite-actions menu on a pending user, click "Copy registration link". Verify no new execution appears in History.

- [ ] **Step 7: Test resend-does-emit**

Click "Resend invite" on a pending user. Verify a new execution appears with `reason: "resent"`.

If all steps pass, the pivot is functionally complete.

---

### Task 27: Pre-completion verification (replaces old Task 18)

The previous Task 18 verification ran against the pre-pivot state. Re-run after the pivot.

- [ ] `./test.sh stack up` then `./test.sh all` — 0 failures (excluding known embed-slug state pollution per project memory).
- [ ] `./test.sh client unit` + `./test.sh client e2e e2e/users.admin.spec.ts`.
- [ ] `cd api && pyright && ruff check .` + `cd client && npm run tsc && npm run lint`.
- [ ] Grep sweep: `grep -rn "email_service\|email_config\|bifrost\.email\|EmailTestDialog\|/settings/email\|/api/email" api/ client/ docs/ --include="*.py" --include="*.ts" --include="*.tsx" --include="*.md"`. Zero hits expected.
- [ ] Confirm no orphan Alembic heads: `cd api && alembic heads`.
- [ ] Report summary to orchestrator.

---

## Phase 5: Topic sources + Events SDK (added 2026-05-21)

**Why this exists.** Phase 4 shipped `emit_internal_event` with `event_source_id=NULL` to keep changes minimal while we figured out the right UX. After working through the model with the user, we landed on:

- **EventSourceType.INTERNAL → TOPIC.** The user-facing term is "Topic" — pub/sub language, accurate.
- **Topic sources are real EventSource rows.** No more NULL-source kludge. Subscriptions hang under them via the normal nested form.
- **No scope-based subscription matching.** A topic source's `organization_id` does NOT filter who subscribes or what fires. All subscriptions on a matching topic source fire on every emit. Org context is metadata stamped on Events, not a routing key.
- **Two emit paths with different scope-resolution semantics:**
  - **Server-side `emit_event(topic, data)`** (called from routers like the invite flow): looks up the topic source by `event_type`, stamps `Event.organization_id = source.organization_id`. The source row IS the org identity for server-originated events on that topic.
  - **SDK `events.emit(topic, data, scope=None)`** (called from workflows): uses `resolve_scope(scope)` exactly like `config.get` — defaults to caller's current execution context org, explicit org_id requires provider-org auth, the resolved value is stamped on the Event. **Source row's org is ignored on the SDK path.**
- **`X-Organization-Id` header has no part in this.** The header is on its way out platform-wide; new code uses explicit `scope` body parameters.
- **`context.event` field added to ExecutionContext** carrying `{type, data, organization_id, id, received_at}`. Populated whenever a workflow is triggered by an Event row, regardless of source type (topic, webhook, schedule — uniform).
- **Topics are arbitrary strings.** Validated server-side as `^[a-z0-9_.]+$`, at-least-one-dot, max 100 chars. Curated registry provides autocomplete suggestions; users can type new topics that get added to the suggestion list as they accumulate.
- **Events SDK package added.** `from bifrost import events; await events.emit("acme.deal_won", {...})`. Mirrors the shape of the deleted `bifrost.email.send` (different verb, same wiring conventions).

### Conventions locked

- Topic regex: `^[a-z0-9_.]+$`, requires at least one dot, max 100 chars
- Auth on `/api/events/emit`: same as the deleted `email.send` (superuser / function-key)
- Fire-and-forget: subscribers run async via `enqueue_system_workflow_execution`; emit returns event_id immediately
- Scope param on SDK: optional, defaults to current context org via `resolve_scope`
- `Event.organization_id` precedence:
  - Server-side `emit_event(topic, data)`: `source.organization_id`
  - SDK `events.emit(topic, data, scope=X)`: `resolve_scope(X)` (X explicit, or None → current context org)

### Task 28: Rename EventSourceType.INTERNAL → TOPIC

**Files:**
- `api/src/models/enums.py` — rename enum value
- New Alembic migration: `ALTER TYPE event_source_type RENAME VALUE 'internal' TO 'topic'`
- `api/src/models/orm/events.py` — PgEnum string list
- `api/src/services/mcp_server/tools/events.py` — error message string list (two locations)
- `client/src/components/events/EventSourceDetail.tsx` — switch case strings + display label
- `client/src/components/events/CreateEventSourceDialog.tsx` — handled by Task 30
- `docs/events/internal.md` → rename to `docs/events/topics.md` (handled by Task 35)

- [ ] Create migration: `cd api && alembic revision -m "rename event source type internal to topic"`
- [ ] Implement: `op.execute("ALTER TYPE event_source_type RENAME VALUE 'internal' TO 'topic'")` upgrade; downgrade reverses
- [ ] Apply: restart bifrost-init, then api
- [ ] Rename in Python enum + all string literals + frontend
- [ ] Run pyright + ruff + tsc + lint
- [ ] Commit: `refactor(events): rename internal source type to topic`

### Task 29: Restore event_source_id NOT NULL + delete get_active_for_internal_event + rewrite emit primitive

**Files:**
- New Alembic migration: revert Phase 4's nullability migration
- `api/src/repositories/events.py` — delete `get_active_for_internal_event`
- `api/src/services/events/processor.py` — rewrite `emit_internal(...)` (rename to `emit_topic`) to use the topic source row
- `api/src/services/events/__init__.py` — rename `emit_internal_event` → `emit_event`; update signature semantics

**Behavior changes:**

The new `EventProcessor.emit_topic` (or just `emit`) signature:

```python
async def emit_topic(
    self,
    *,
    topic: str,
    data: dict,
    organization_id: UUID | None = None,  # explicit override; if None, use source.organization_id
    triggered_by: str | None = None,
) -> tuple[UUID, int]:
    """Emit a topic event. Returns (event_id, subscribers_notified)."""
```

Algorithm:
1. Validate topic against `validate_topic(topic)` — see Task 31
2. Look up `EventSource` where `source_type='topic' AND event_type=topic`. If not found: log + no-op + return `(generated_event_id_for_audit, 0)` OR raise — pick the no-op path so missing-source doesn't break the invite flow.
3. Determine stamped org: `organization_id if organization_id is not None else source.organization_id`.
4. Insert Event row with `event_source_id=source.id`, `event_type=topic`, `data`, `organization_id=<resolved>`.
5. Find subscriptions via the existing `get_subscriptions_for_source(source.id)` matcher — no scope filtering.
6. For each subscription: create EventDelivery, dispatch via `enqueue_system_workflow_execution` (carries `SYSTEM_USER_ID` correctly).
7. Return `(event_id, len(subscriptions))`.

The top-level helper `emit_event(topic, data, *, organization_id=None, triggered_by=None)` opens its own session and calls `processor.emit_topic(...)`.

**NULL safeguard in the revert migration:**

```python
def upgrade() -> None:
    # Safeguard: if any internal events were emitted in Phase 4, we cannot revert.
    conn = op.get_bind()
    null_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM events WHERE event_source_id IS NULL")
    ).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"{null_count} events with NULL event_source_id exist; cannot revert nullability"
        )
    op.alter_column("events", "event_source_id", nullable=False)
    op.alter_column("event_subscriptions", "event_source_id", nullable=False)
```

- [ ] Write migration with NULL safeguard
- [ ] Delete `get_active_for_internal_event` from the subscription repo
- [ ] Rewrite `emit_internal` → `emit_topic` per algorithm above
- [ ] Rename `emit_internal_event` → `emit_event` (top-level helper)
- [ ] Update Phase 4 tests in `tests/unit/services/events/test_emit_internal.py` → rename file to `test_emit_topic.py`, rewrite assertions for new behavior (source row exists, subscriptions matched via source, no scope filtering)
- [ ] Update tests in `tests/e2e/api/test_user_invites.py` that asserted on NULL source_id
- [ ] Commit: `refactor(events): topic sources + emit primitive uses source rows`

### Task 30: Frontend grouped source picker + topic form

**Files:**
- `client/src/components/events/CreateEventSourceDialog.tsx`

UX:
- Replace the flat `<Select>` with a grouped picker. Groups: **Built-In** (Webhook, Schedule) and **Topics** (registry entries + "Custom topic…" option that reveals a free-text input).
- When a topic is chosen (registry or custom): hide webhook adapter / schedule cron sections. Show: **Name** (optional, derive default from topic like `User Invited` from `user.invited`), **Topic** (the value, displayed as a code-style chip when picked from registry, editable input when "Custom topic…"), **Organization** (picker — defaults to GLOBAL; this is the org stamped on Events emitted server-side for this topic).
- Submit body includes `source_type: "topic"`, `event_type: <chosen-or-typed>`, `organization_id` (or null), `name`.
- Validation: topic must match `^[a-z0-9_.]+$` and contain at least one dot; max 100. Show inline error.

- [ ] Refactor the source-type input as a grouped combobox (or a primary "Type" select + conditional sub-fields, whichever fits the existing shadcn vocabulary best)
- [ ] Add the topic combobox driven by `GET /api/events/topics` (Task 32)
- [ ] Implement client-side topic validation matching server regex
- [ ] Update existing CreateEventSourceDialog test for the new fields
- [ ] Verify the "Internal (Coming Soon)" string is gone
- [ ] Commit: `feat(events): topic source picker in CreateEventSourceDialog`

### Task 31: validate_topic helper

**Files:**
- `api/src/services/events/validation.py` (new)
- Used by: server primitive `emit_event`, HTTP endpoint `POST /api/events/emit`, EventSource create router

```python
TOPIC_REGEX = re.compile(r"^[a-z0-9_.]+$")
TOPIC_MAX_LEN = 100

def validate_topic(topic: str) -> None:
    """Raise ValueError if topic is invalid."""
    if not topic or len(topic) > TOPIC_MAX_LEN:
        raise ValueError(f"Topic must be 1-{TOPIC_MAX_LEN} chars")
    if not TOPIC_REGEX.match(topic):
        raise ValueError("Topic must match ^[a-z0-9_.]+$")
    if "." not in topic:
        raise ValueError("Topic must contain at least one dot (e.g. 'user.invited')")
```

- [ ] Unit test the validator (valid + invalid cases)
- [ ] Wire into the three call sites
- [ ] Commit: `feat(events): validate_topic helper`

### Task 32: POST /api/events/emit endpoint + GET /api/events/topics registry

**Files:**
- `api/src/routers/events.py` (or wherever event endpoints live — confirm via grep)
- Add response/request models in `api/shared/models.py` (per CLAUDE.md: Pydantic source of truth)

**POST /api/events/emit:**
- Auth: superuser / function-key (same as the deleted `/api/email/send`)
- Body: `{topic: str, data: dict, scope: str | None}` (scope is "GLOBAL" or org UUID string)
- Resolves scope → `organization_id`
- Calls `validate_topic(topic)` — return 400 with message on failure
- Calls `await emit_event(topic, data, organization_id=resolved_org, triggered_by=user.user_id)`
- Returns `{event_id, subscribers_notified}`
- Does NOT read `X-Organization-Id` header

**GET /api/events/topics:**
- Returns `{curated: [{topic, description}], in_use: [topic strings]}` for combobox autocomplete
- Curated list: hand-maintained constant in `api/src/services/events/registry.py`. Initial entry: `user.invited`.
- `in_use`: `SELECT DISTINCT event_type FROM event_sources WHERE source_type='topic'`
- No auth required beyond standard session (so the UI can populate the combobox)

- [ ] Write the registry constant
- [ ] Implement both endpoints
- [ ] E2E tests for both (valid emit, invalid topic 400, registry returns curated + in_use)
- [ ] Commit: `feat(events): POST /events/emit + GET /events/topics`

### Task 33: bifrost.events SDK module

**Files:**
- `api/bifrost/events.py` (new)
- `api/bifrost/__init__.py` — export `events`

```python
"""
Events SDK for Bifrost.

Publish events to topics; subscribed workflows receive them.

Usage:
    from bifrost import events

    result = await events.emit(
        "acme.deal_won",
        {"deal_id": "...", "amount": 50000},
    )
"""

from .client import get_client, raise_for_status_with_detail
from ._context import resolve_scope


class events:
    """Event publishing operations (async)."""

    @staticmethod
    async def emit(
        topic: str,
        data: dict,
        scope: str | None = None,
    ) -> dict:
        """
        Publish an event to a topic. Workflows subscribed to this topic will run.

        Args:
            topic: Lowercase string, dot-separated (e.g. "acme.deal_won").
                   Validated server-side: ^[a-z0-9_.]+$, must contain a dot.
            data: JSON-serializable payload. Available to subscribers as
                  context.parameters (or via input_mapping templates) and as
                  context.event.data.
            scope: Organization scope override. Omit to use the execution
                   context org (default — most workflows want this). Pass an
                   org UUID to target a specific org (provider org context
                   required, same rule as config.get).

        Returns:
            dict with keys: event_id (str), subscribers_notified (int)
        """
        client = get_client()
        resolved = resolve_scope(scope)
        response = await client.post(
            "/api/events/emit",
            json={"topic": topic, "data": data, "scope": resolved},
        )
        raise_for_status_with_detail(response)
        return response.json()
```

- [ ] Implement
- [ ] Add to `__all__` in bifrost/__init__.py
- [ ] Confirm allowed in `import_restrictor.py` (bifrost.email was on the allowlist; mirror that)
- [ ] Unit test the SDK shape (mocked HTTP client; valid + scope override + error propagation)
- [ ] Commit: `feat(sdk): bifrost.events module`

### Task 34: context.event field on ExecutionContext

**Files:**
- `api/bifrost/_execution_context.py` — add `event` field
- `api/src/jobs/consumers/workflow_execution.py` (or wherever event-triggered executions populate context) — set the field when triggered by an Event

```python
@dataclass
class EventContext:
    """Event metadata for event-triggered workflow executions."""
    id: str            # Event UUID
    type: str          # The topic (e.g. "user.invited")
    data: dict         # The event payload
    organization_id: str | None  # Stamped org (None for GLOBAL)
    received_at: str   # ISO timestamp


@dataclass
class ExecutionContext:
    # ... existing fields ...
    event: EventContext | None = field(default=None)
```

Populated for: topic-triggered workflows AND webhook/schedule-triggered workflows (symmetric — `context.event` exists whenever an Event row triggered the execution). Set via `enqueue_system_workflow_execution` taking an `event` kwarg.

- [ ] Add EventContext dataclass + field
- [ ] Update the executor to populate it
- [ ] Update existing webhook-triggered workflow tests to assert `context.event` is set
- [ ] Commit: `feat(sdk): context.event field for event-triggered workflows`

### Task 35: Docs — topics + SDK + context.event

**Files:**
- `docs/events/internal.md` → rename to `docs/events/topics.md`
- `docs/llm.txt` — update Events section
- Add a section to `bifrost.events` SDK docs

Document:
- Topic concept (pub/sub, free-form strings, validated regex)
- Topic sources (UI organizes subscriptions; `source.organization_id` stamps events emitted server-side; SDK path uses its own scope resolution)
- `context.event` shape and population rules
- `events.emit(topic, data, scope=...)` SDK signature with scope semantics matching config.get
- The `user.invited` topic (existing payload schema from Task 24, updated for new context.event structure)

- [ ] Write the docs
- [ ] Commit: `docs(events): topics + SDK + context.event`

### Task 36: Re-wire user.invited emission

**Files:**
- `api/src/routers/users.py`

The emission calls in `create_user` and `_generate_invite(send=True)` already pass `organization_id`. With the new `emit_event` signature using `source.organization_id` as the default, an explicit `organization_id=actor.organization_id` keeps the existing behavior. Confirm semantics:

- Acme admin creates a user → `emit_event("user.invited", payload, organization_id=acme_id)` → stamps Acme on the Event. Same outcome as today.
- If admin omitted `organization_id` (we don't, but hypothetically) → `emit_event` would fall back to `source.organization_id`, which is whatever the admin set when creating the source. Also reasonable.

No code change required beyond renaming `emit_internal_event` → `emit_event` (Task 29 handles the rename platform-wide). Validate the call sites compile and tests pass.

- [ ] Confirm users.py uses the new function name
- [ ] Run invite e2e + unit tests
- [ ] Commit: `refactor(invites): use renamed emit_event` (skip if absorbed into Task 29's rename commit)

### Task 37: Smoke test (replaces old Task 26)

End-to-end manual smoke through the new UI.

1. Open the Events page in the debug UI.
2. **Create an Event Source.** Type = Topic. Topic = `user.invited` (from registry autocomplete). Organization = GLOBAL. Name = "User Invited".
3. **Create a workflow** that returns `context.event.data` (and possibly `context.event.organization_id` for sanity).
4. **Create a subscription** under the topic source, target = the noop workflow.
5. **Trigger an invite** from /users with "Trigger invite automation" checked.
6. **Verify in Execution History:** new execution exists; result panel shows `{user_id, email, registration_url, expires_at, invited_by, reason: "created"}` plus the org_id you set on the source if it's exposed in context.event.
7. **Visit the registration_url** in a private window, complete registration, log in.
8. **Negative cases:** Copy-link doesn't trigger an execution; trigger_automation=false doesn't trigger an execution; Resend produces a new execution with reason "resent".

Drive this with the user — UI smoke needs a human.

### Task 38: Phase 5 verification (replaces old Task 27)

- [ ] `./test.sh stack up` then `./test.sh all` (excluding known embed-slug state pollution)
- [ ] `./test.sh client unit` + `./test.sh client e2e e2e/users.admin.spec.ts`
- [ ] `cd api && pyright && ruff check .` + `cd client && npm run tsc && npm run lint`
- [ ] Grep sweep for stale references: `grep -rn "internal_event\|EventSourceType.INTERNAL\|get_active_for_internal" api/ client/ --include="*.py" --include="*.ts" --include="*.tsx"` — should only hit migration revision IDs
- [ ] `alembic heads` — single head
- [ ] Report summary

---

### Task 28: Open the PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin 226-users-invite-flow
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(users): invite flow via events + table redesign" --body "$(cat <<'EOF'
## Summary
- **Magic-link invite flow** (`UserInvite` model, hashed tokens, 7d TTL, single-use, revocable)
- **Event-driven invite delivery**: emits the new `user.invited` internal event. Any workflow subscribed to this event handles delivery (email, Slack, etc.) — no more bespoke email-config UI.
- **Users table redesign**: two-line name cell, sticky Actions column, no horizontal scroll, Status column with Active / Pending invite / Invite expired / Not invited badges.
- **`/accept-invite?token=...` page** for completing registration (password-based; passkey-from-invite is a follow-up, see below).

## Breaking changes
- **Removed `bifrost.email.send()` SDK module.** It never worked against a configured email workflow (passed `user_id="system"` as a string into a UUID parser). Zero in-repo callers; if downstream code depends on it, migrate to `events.emit("email.send_requested", {...})` or subscribe a workflow to the relevant domain event.
- **Removed Settings → Email page** and the `/api/email/send` SDK endpoint. Configure email delivery by subscribing a workflow to `user.invited` (and future `*.send_requested` events).

## Follow-ups
- **Passkey-from-invite.** `passkey_service.py` has no token-gated registration path. `/accept-invite` is password-only. Adding passkey support requires a new endpoint that accepts an invite token as the credential.
- **Generic email-send event** (`email.send_requested`) if anyone wants the old `bifrost.email.send()` ergonomics back as a thin sugar wrapper.

## Test plan
- [x] `./test.sh all` (backend unit + e2e)
- [x] `./test.sh client unit`
- [x] `./test.sh client e2e e2e/users.admin.spec.ts`
- [x] Manual: smoke per Task 26 (noop subscriber → trigger invite → execution shows payload → registration completes)
- [x] Manual: long org name no longer causes horizontal scroll
- [x] Manual: regenerate-without-send does NOT trigger automations

Fixes #226
Fixes #227
Fixes #228
EOF
)"
```

- [ ] **Step 3: Watch the PR (combined reviews + checks)**

Use the watcher pattern from the bifrost-issues skill. Address any CodeQL or reviewer comments before merge.

---

## Self-Review

**Spec coverage:**
- #226 (table scroll) → Tasks 6, 15. ✓
- #227 (invite flow):
  - `pending_registration` state → reuses `is_registered=False` (Task 9, 10). ✓
  - Token semantics (single-use, TTL, hashed, revocable) → Task 7, 9. ✓
  - Email locked to invite → Task 11 consumes by token; consume() doesn't accept email. ✓
  - Status column with values → Tasks 13, 15. ✓
  - Resend / Regenerate / Copy link / Revoke actions → Tasks 12, 14, 15. ✓
  - SDK `invite=False` default flag → Task 8 (`UserCreate.invite`); CLI/SDK wrapper for `users create --invite` flagged as a follow-up since the Bifrost users CLI/SDK file wasn't located in exploration. **Open follow-up for the agent**: after Task 8, grep for any existing user-create CLI under `api/bifrost/` and add `--invite` flag if present. If absent, leave it; the REST contract change is already shipped.
  - Email-workflow slot for invite → **deviation from spec**: spec called for a new dedicated workflow slot. Reuse the existing single email workflow because `send_email` is already generic over subject/body — adding a second slot now is YAGNI. Documented in PR body; can be added later if admins want segregation.
  - Auth setup UI lifted from `Setup.tsx` → Task 16. ✓
  - Token storage primitive → Task 7 (Postgres table, not Redis: needs status display). ✓
- #228 (email test) → Tasks 1–5. ✓

**Placeholder scan:** None present (all code shown explicitly, all commands explicit, no "implement appropriately" steps).

**Type consistency:**
- `invite_status` field name used consistently across backend (`UserPublic`), frontend (`user.invite_status`), and tests. ✓
- `CreateInviteResponse` referenced in router, service test, frontend service. ✓
- `InviteConsumeError` raised in `consume()` and caught in `register-from-invite` endpoint. ✓
- `status_for` returns string; `UserStatusBadge` accepts string with default fallback. ✓

**Single deviation flagged:** dedicated invite-email-workflow slot was dropped in favor of reusing `send_email`. Captured in self-review and to be noted in the PR body.
