# Routers

API route handlers for Bifrost. Each file contains related endpoints.

---

## Forms

Reusable UI components that collect user input and execute workflows. Forms provide a structured way to gather parameters before triggering automations.

### Architecture

**Dual-write persistence:**
- **PostgreSQL**: Fast queries, org scoping, access control, relational data
- **S3**: Source control integration, deployment portability, workspace sync

**Normalized schema:**
```
forms (parent)
  └── form_fields (1:N, ordered by position)
  └── form_roles (N:M with roles table)
```

### Key Files

| File | Purpose |
|------|---------|
| `routers/forms.py` | HTTP endpoint handlers |
| `models/orm/forms.py` | SQLAlchemy ORM (Form, FormField, FormRole) |
| `models/contracts/forms.py` | Pydantic request/response models |
| `repositories/forms.py` | Data access with org scoping |

### Access Control

Forms use layered access control:

1. **Organization scoping** - Forms belong to an org (or are global with `org_id=NULL`)
2. **Access level** (`access_level` enum):
   - `authenticated` - Any authenticated user in the org can access
   - `role_based` - Only users with assigned roles can access
3. **Role assignments** - `form_roles` table links forms to roles for fine-grained permissions

### Form Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                         CREATE FORM                              │
│  POST /api/forms                                                 │
│  - Define fields (text, select, file, etc.)                      │
│  - Set access_level and role assignments                         │
│  - Dual-write to PostgreSQL + S3                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CONFIGURE FORM                             │
│  - Link startup workflow (launch_workflow_id)                    │
│  - Configure data providers for dynamic field options            │
│  - Set main execution workflow (workflow_id)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        EXECUTE FORM                              │
│                                                                  │
│  1. POST /{form_id}/startup                                      │
│     └── Runs launch_workflow_id for form initialization          │
│                                                                  │
│  2. Data providers execute as user fills fields                  │
│     └── Dynamic options, validation, dependent fields            │
│                                                                  │
│  3. POST /{form_id}/execute                                      │
│     └── Runs workflow_id with collected form inputs              │
└─────────────────────────────────────────────────────────────────┘
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/forms` | List forms (org-scoped + global) |
| `POST` | `/api/forms` | Create form (dual-write DB + S3) |
| `GET` | `/api/forms/{form_id}` | Get form by ID |
| `PATCH` | `/api/forms/{form_id}` | Update form fields |
| `PUT` | `/api/forms/{form_id}` | Full form replacement |
| `DELETE` | `/api/forms/{form_id}` | Delete form |
| `POST` | `/api/forms/{form_id}/startup` | Run startup workflow |
| `POST` | `/api/forms/{form_id}/submissions` | Validate and submit the linked workflow |
| `POST` | `/api/forms/{form_id}/upload` | Upload file for file fields |
