"""Regulated-tier feature endpoints: retention policy, field encryption status, DPA templates.

All endpoints in this router require a Regulated license tier.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.database import get_session
from app.core.license_gate import require_feature
from app.models.workspace import Workspace

router = APIRouter(tags=["regulated"])


# ---------------------------------------------------------------------------
# Retention Policy  (retention_policy)
# ---------------------------------------------------------------------------

class RetentionPolicyOut(BaseModel):
    workspace_id: int
    data_retention_days: Optional[int]
    background_loop_active: bool


class RetentionPolicyIn(BaseModel):
    data_retention_days: Optional[int] = None  # None = disabled


@router.get(
    "/workspaces/{workspace_id}/retention-policy",
    response_model=RetentionPolicyOut,
    dependencies=[Depends(require_feature("retention_policy"))],
)
async def get_retention_policy(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Return the data retention policy for this workspace.

    The background data-retention loop purges terminal runs older than
    data_retention_days. A null value means no automatic purging.
    """
    if workspace_id not in ctx.workspace_ids:
        raise HTTPException(403, "Forbidden")
    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")
    return RetentionPolicyOut(
        workspace_id=workspace_id,
        data_retention_days=ws.data_retention_days,
        background_loop_active=bool(
            os.environ.get("DATA_RETENTION_DAYS") or ws.data_retention_days
        ),
    )


@router.put(
    "/workspaces/{workspace_id}/retention-policy",
    response_model=RetentionPolicyOut,
    dependencies=[Depends(require_feature("retention_policy"))],
)
async def set_retention_policy(
    workspace_id: int,
    body: RetentionPolicyIn,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Configure the data retention period for runs in this workspace.

    Set data_retention_days to a positive integer (minimum 7) to automatically
    purge terminal runs older than that many days. Set to null to disable.
    The background loop runs every 4 hours; changes take effect on the next cycle.
    """
    if workspace_id not in ctx.workspace_ids or ctx.role != "admin":
        raise HTTPException(403, "Admin role required")
    if body.data_retention_days is not None and body.data_retention_days < 7:
        raise HTTPException(422, "Minimum retention period is 7 days")
    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")
    ws.data_retention_days = body.data_retention_days
    session.add(ws)
    await session.commit()
    return RetentionPolicyOut(
        workspace_id=workspace_id,
        data_retention_days=ws.data_retention_days,
        background_loop_active=bool(
            os.environ.get("DATA_RETENTION_DAYS") or ws.data_retention_days
        ),
    )


# ---------------------------------------------------------------------------
# Field Encryption Status  (field_encryption)
# ---------------------------------------------------------------------------

class EncryptionStatusOut(BaseModel):
    enabled: bool
    algorithm: Optional[str] = None
    key_length_bits: Optional[int] = None
    sentinel: str


@router.get(
    "/workspaces/{workspace_id}/encryption-status",
    response_model=EncryptionStatusOut,
    dependencies=[Depends(require_feature("field_encryption"))],
)
async def get_encryption_status(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return field-level encryption status for this platform instance.

    Encryption is platform-wide (controlled by ANTCREW_ENCRYPTION_KEY). When
    enabled, sensitive JSON columns are encrypted at rest using AES-256-GCM.
    The ANTCREW_ENCRYPTION_KEY env var is never exposed via this endpoint.
    """
    if workspace_id not in ctx.workspace_ids:
        raise HTTPException(403, "Forbidden")
    from app.core.encryption import _KEY, _SENTINEL  # noqa: PLC0415

    enabled = _KEY is not None
    return EncryptionStatusOut(
        enabled=enabled,
        algorithm="AES-256-GCM" if enabled else None,
        key_length_bits=len(_KEY) * 8 if enabled else None,
        sentinel=_SENTINEL,
    )


# ---------------------------------------------------------------------------
# DPA Templates  (dpa_templates)
# ---------------------------------------------------------------------------

_DPA_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Data Processing Agreement — {ws_name}</title>
<style>
body {{ font-family: Georgia, serif; max-width: 800px; margin: 40px auto; padding: 0 24px;
       line-height: 1.75; color: #1a1a1a; font-size: 14px; }}
h1 {{ font-size: 1.5rem; font-family: 'Helvetica Neue', sans-serif; margin-bottom: 0.25rem; }}
h2 {{ font-size: 1rem; font-family: 'Helvetica Neue', sans-serif; font-weight: 600;
     margin: 2rem 0 0.5rem; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
.meta {{ color: #666; font-size: 0.85rem; margin-bottom: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 0.75rem 0 1.25rem; }}
td, th {{ border: 1px solid #ccc; padding: 7px 12px; font-size: 0.88rem; }}
th {{ background: #f5f5f5; font-weight: 600; font-family: 'Helvetica Neue', sans-serif; }}
ol {{ padding-left: 1.5rem; }}
li {{ margin-bottom: 0.35rem; }}
.signatures {{ display: flex; gap: 4rem; margin-top: 3.5rem; }}
.sig {{ flex: 1; border-top: 1px solid #333; padding-top: 10px; font-size: 0.85rem; }}
@media print {{ body {{ margin: 20px; }} }}
</style>
</head>
<body>

<h1>Data Processing Agreement</h1>
<p class="meta">
  Document ref: DPA-{ws_slug}-{date_compact}<br>
  Prepared: {date}<br>
  Workspace: <strong>{ws_name}</strong>
</p>

<h2>1. Parties</h2>
<table>
<tr><th>Role</th><th>Entity</th><th>Tax ID / Registration</th></tr>
<tr><td>Data Controller ("Controller")</td><td>{billing_name}</td><td>{billing_nif}</td></tr>
<tr><td>Data Processor ("Processor")</td><td>antcrew — Iago Pueyo Morales</td><td>NIF 47481393Q</td></tr>
</table>

<h2>2. Subject matter</h2>
<p>
This Agreement governs the processing of personal data by the Processor on behalf of the Controller
in connection with AI agent orchestration services provided through the antcrew platform.
It is effective from the later of the date of signature by both parties or the date the Controller
first uses the platform, and remains in force for the duration of the service subscription.
</p>

<h2>3. Nature and purposes of processing</h2>
<p>
The Processor will process data to: execute AI pipeline runs; store run artifacts and audit logs;
facilitate human-in-the-loop (HITL) review workflows; generate compliance certificates; and provide
billing and account management functionality.
Processing is performed exclusively on documented instructions from the Controller.
</p>

<h2>4. Categories of data subjects and data</h2>
<table>
<tr><th>Category</th><th>Examples</th></tr>
<tr><td>Run inputs and outputs</td><td>Text, code, structured payloads supplied to agents by the Controller</td></tr>
<tr><td>Operational metadata</td><td>Timestamps, model identifiers, token counts, latency, cost estimates</td></tr>
<tr><td>User identifiers</td><td>API key hashes, workspace IDs, reviewer assignments, audit actor references</td></tr>
<tr><td>Billing information</td><td>Entity name, tax ID, billing address (Controller-supplied)</td></tr>
</table>

<h2>5. Obligations of the Processor (Art. 28 GDPR)</h2>
<ol>
<li>Process personal data only on documented instructions from the Controller (Art. 28(3)(a)).</li>
<li>Ensure that persons authorised to process personal data have committed to confidentiality (Art. 28(3)(b)).</li>
<li>Implement appropriate technical and organisational measures to ensure a level of security
    appropriate to the risk (Art. 32), including AES-256-GCM encryption of sensitive fields at rest
    when ANTCREW_ENCRYPTION_KEY is configured.</li>
<li>Respect the conditions for engaging sub-processors and inform the Controller of any intended
    changes (Art. 28(2), 28(3)(d)).</li>
<li>Assist the Controller in responding to data subject requests (Art. 28(3)(e)).</li>
<li>Assist the Controller with obligations under Arts. 32–36 (security, breach notification, DPIAs).</li>
<li>Delete or return all personal data at the end of the service, and delete existing copies
    unless storage is required by EU/Member State law (Art. 28(3)(g)).</li>
<li>Make available all information necessary to demonstrate compliance and allow for audits
    (Art. 28(3)(h)).</li>
</ol>

<h2>6. Sub-processors</h2>
<p>The Controller hereby grants general authorisation for the Processor to engage the following sub-processors:</p>
<table>
<tr><th>Sub-processor</th><th>Country</th><th>Purpose</th><th>Safeguard</th></tr>
<tr><td>Anthropic PBC</td><td>USA</td><td>Claude API (managed-key mode)</td><td>SCCs (2021/914)</td></tr>
<tr><td>Fly.io, Inc.</td><td>USA / EU</td><td>Platform hosting (region: {fly_region})</td><td>SCCs + DPA</td></tr>
<tr><td>Stripe, Inc.</td><td>USA</td><td>Payment processing</td><td>SCCs + DPA</td></tr>
</table>

<h2>7. International transfers</h2>
<p>
Where personal data is transferred outside the European Economic Area, the Processor relies on
Standard Contractual Clauses (SCCs) adopted by the European Commission (Decision 2021/914).
Copies are available on request via privacy@antcrew.org.
</p>

<h2>8. Data retention</h2>
<p>
Run data for this workspace is retained for <strong>{retention_days}</strong>.
The Controller may configure shorter periods via the platform retention-policy setting.
Upon termination of the subscription, the Processor will delete all personal data within 30 days
unless the Controller requests earlier deletion or applicable law requires longer retention.
</p>

<h2>9. Security incident notification</h2>
<p>
The Processor will notify the Controller without undue delay (and within 72 hours where feasible)
upon becoming aware of a personal data breach affecting Controller data.
</p>

<h2>10. Governing law and jurisdiction</h2>
<p>
This Agreement is governed by Spanish law. Any disputes shall be submitted to the exclusive
jurisdiction of the courts of Spain.
</p>

<div class="signatures">
  <div class="sig">
    <p><strong>For the Controller</strong></p>
    <br><br>
    <p>Signed: ___________________________</p>
    <p>Name: {billing_name}</p>
    <p>Date: _______________</p>
  </div>
  <div class="sig">
    <p><strong>For the Processor</strong></p>
    <br><br>
    <p>Signed: ___________________________</p>
    <p>Name: Iago Pueyo Morales / antcrew</p>
    <p>Date: _______________</p>
  </div>
</div>

</body>
</html>"""


@router.get(
    "/workspaces/{workspace_id}/dpa-template",
    response_class=HTMLResponse,
    dependencies=[Depends(require_feature("dpa_templates"))],
)
async def get_dpa_template(
    workspace_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session=Depends(get_session),
):
    """Generate a GDPR Art. 28 Data Processing Agreement pre-filled with workspace billing data.

    Returns a printable HTML document. Download, print or export to PDF, then
    obtain signatures from both parties to evidence GDPR DPA compliance.
    """
    if workspace_id not in ctx.workspace_ids:
        raise HTTPException(403, "Forbidden")
    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")

    now = datetime.now(timezone.utc)
    retention = (
        f"{ws.data_retention_days} days"
        if ws.data_retention_days
        else "the duration of the subscription"
    )
    html = _DPA_TEMPLATE.format(
        ws_name=ws.name,
        ws_slug=ws.slug,
        billing_name=ws.billing_razon_social or ws.name,
        billing_nif=ws.billing_nif or "(not provided)",
        date=now.strftime("%d %B %Y"),
        date_compact=now.strftime("%Y%m%d"),
        retention_days=retention,
        fly_region=os.environ.get("FLY_REGION", "EU/unspecified"),
    )
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": f'inline; filename="DPA-{ws.slug}.html"'},
    )
