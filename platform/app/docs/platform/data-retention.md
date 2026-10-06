# Data retention

antcrew-platform stores several types of data with different lifetimes. This page documents
what is kept, for how long, and what that means for compliance.

---

## Retention policies

| Table | Default lifetime | Configurable via | Notes |
|---|---|---|---|
| `event` | 30 days | `DATA_RETENTION_DAYS` | Engine events (agent.start, agent.end, …) |
| `webhook_delivery` | 30 days | `DATA_RETENTION_DAYS` | Only terminal rows (delivered, failed) |
| `discovery_session` | 7 days (last activity) | `DISCOVERY_SESSION_TTL_DAYS` | Deleted by background job |
| `run` | **indefinite** | — | See below |
| `ticket` | **indefinite** | — | Linked to runs |
| `hitl_review` | **indefinite** | — | Linked to runs |

Cleanup runs on background tasks: events and webhook deliveries are swept daily; discovery
sessions are swept every 6 hours.

---

## What `run.request` contains

The `run.request` column stores the exact free-form text submitted by the caller — the
natural-language description of the work to perform. This may include:

- Feature descriptions, bug reports, business requirements
- Code snippets or configuration fragments
- References to internal systems, employees, or customers

**This column is never automatically deleted.** Runs form the audit trail for completed
work and are retained indefinitely by default.

---

## GDPR and LOPDGDD considerations (Spain)

antcrew is operated by a company established in Spain and is subject to the GDPR
(Regulation (EU) 2016/679) and the LOPDGDD (Organic Law 3/2018).

**Key points:**

- `run.request` is potentially personal data if it identifies individuals or can be linked
  to an identified natural person. Under GDPR Art. 5(1)(e), personal data must not be kept
  longer than necessary for the purpose for which it was collected.

- If a user exercises their right to erasure (GDPR Art. 17), use the API endpoints
  described in [GDPR erasure API](#gdpr-erasure-api) below. Manual SQL deletion is also
  documented for self-hosted operators without API access.

- The current indefinite retention of runs is justifiable for operational and audit purposes.
  If your deployment has stricter retention obligations, consider adding a `RUN_RETENTION_DAYS`
  env var to the platform configuration and opening an issue to track the implementation.

- `event` and `webhook_delivery` rows are deleted after `DATA_RETENTION_DAYS` (default: 30).
  These tables may contain run IDs and partial request context in event payloads — the
  30-day window is a reasonable balance between operational observability and data minimisation.

---

## Configuration

Set in `.env.prod` on your Hetzner (or self-hosted) server:

```env
# How long to retain event and webhook_delivery rows (days)
DATA_RETENTION_DAYS=30

# How long to retain idle discovery sessions (days of inactivity)
DISCOVERY_SESSION_TTL_DAYS=7
```

---

## Manual deletion

To delete a specific run and all associated data, execute in order (no cascading FK):

```sql
DELETE FROM event        WHERE run_id = 'run_01...';
DELETE FROM ticket       WHERE run_id = 'run_01...';
DELETE FROM hitl_review  WHERE run_id = 'run_01...';
DELETE FROM run          WHERE run_id = 'run_01...';
```

To wipe all data for a workspace (e.g., on account deletion):

```sql
-- Get all run_ids first
SELECT run_id FROM run WHERE workspace_id = <id>;

-- Then delete in order
DELETE FROM event       WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = <id>);
DELETE FROM ticket      WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = <id>);
DELETE FROM hitl_review WHERE run_id IN (SELECT run_id FROM run WHERE workspace_id = <id>);
DELETE FROM run         WHERE workspace_id = <id>;
DELETE FROM workspace   WHERE id = <id>;
```

!!! warning "No cascade"
    SQLModel relationships are not configured with `ondelete="CASCADE"` on the run-level
    tables. Always delete child rows before parent rows to avoid foreign-key violations.

---

## GDPR erasure API

The platform provides two admin-only endpoints for GDPR Art. 17 erasure requests.
Both require a platform-admin API key.

### Erase a user (anonymise PII)

`POST /admin/users/{user_id}/erase`

Anonymises account PII and erases run request content across all workspaces the user
belonged to. Irreversible.

**What it does:**

- Replaces `user.email` with `erased_{id}@erased.antcrew`
- Clears `display_name`, `password_hash`, `totp_secret`
- Replaces `run.request` with `[erased {timestamp}]` for all runs in the user's workspaces
- Deletes all discovery sessions for those workspaces
- Revokes all active API keys
- Deletes all browser sessions

**Response:**

```json
{
  "erased_at": "2026-08-23T10:00:00",
  "user_id": 42,
  "email_anonymised": "erased_42@erased.antcrew",
  "runs_request_erased": 187,
  "discovery_sessions_deleted": 3,
  "api_keys_revoked": 2,
  "browser_sessions_deleted": 1,
  "workspaces_affected": [7, 12]
}
```

### Delete a workspace (account closure / full erasure)

`DELETE /admin/workspaces/{workspace_id}`

Deletes all data for a workspace and the workspace itself. Use for account closure or
when a workspace-level erasure request is received. Irreversible.

**Deletion order (respects FK constraints):**

1. `agent_event` rows linked to workspace runs
2. `event` rows linked to workspace runs
3. `ticket` rows linked to workspace runs
4. `hitl_review` rows linked to workspace runs
5. `webhook_delivery` rows linked to workspace runs
6. `run` rows for the workspace
7. `api_key` rows for the workspace
8. `workspacemembership` rows
9. `discoverysession` rows
10. `webhookconfig` rows
11. `workspace` row

**Response:**

```json
{
  "workspace_id": 7,
  "deleted_at": "2026-08-23T10:00:00+00:00",
  "rows_deleted": {
    "runs": 187,
    "events": 4210,
    "tickets": 94,
    "api_keys": 3,
    ...
  }
}
```

### When to use which endpoint

| Scenario | Endpoint |
|---|---|
| Individual user requests erasure under Art. 17 | `POST /admin/users/{user_id}/erase` |
| Workspace cancels and requests full data deletion | `DELETE /admin/workspaces/{workspace_id}` |
| GDPR erasure for a user who was member of multiple workspaces | `POST /admin/users/{user_id}/erase` (covers all workspaces) |

---

## See also

- [Privacy Policy](privacy-policy.md) — full data processing description and data subject rights
- [DPA template](dpa-template.md) — Data Processing Agreement for customers who use antcrew as a Processor
- [Compliance Pack](compliance-pack.md) — bulk attestation export and compliance officer dashboard
