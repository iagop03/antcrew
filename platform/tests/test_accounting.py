"""Tests for the accounting module (round 21).

Covers:
A1  GET /accounting/receipts/ → 401 without session
A2  GET /accounting/receipts/ → 403 when accounting_access=False
A3  PATCH /admin/users/{id}/accounting → toggles accounting_access
R1  POST /accounting/receipts/ → creates receipt (no file)
R2  GET /accounting/receipts/ → lists all receipts
R3  GET /accounting/receipts/{id} → 404 for unknown id
R4  PUT /accounting/receipts/{id} → updates editable fields
R5  PUT verified receipt → 409
R6  DELETE pending receipt → 204
R7  DELETE verified receipt → 409
R8  POST /accounting/receipts/{id}/verify without file → 409
I1  POST /accounting/invoices/ → creates with auto-number ANT-{year}-001
I2  POST second invoice same year → ANT-{year}-002
I3  invoice_type auto-detection: ES→nacional, DE→intracomunitaria, US→exportacion
I4  GET /accounting/invoices/ → list
I5  POST /accounting/invoices/{id}/void → void
I6  POST /accounting/invoices/{id}/void again → 409
S1  GET /accounting/summary → zeros when empty
S2  GET /accounting/summary → correct P&L aggregation
V1  GET /accounting/vat-report → correct quarter period dates
V2  GET /accounting/vat-report → nacional vs intracomunitario breakdown
M1  GET /accounting/modelo349 → empty when no intracomunitaria invoices
M2  GET /accounting/modelo349 → only shows intracomunitaria
M3  GET /accounting/modelo349 → alert when NIF missing
E1  GET /accounting/export → zip content-type
B1  GET /workspaces/{id}/billing-profile → empty profile, complete=False
B2  PATCH /workspaces/{id}/billing-profile → saves fields, complete=True
B3  PATCH billing-profile invalid entity_type → 422
B4  PATCH billing-profile → NIF uppercased
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.accounting import Invoice, Receipt
from app.models.auth import User, UserSession
from app.models.run import Workspace

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CSRF = "test-csrf-acct"
_CSRF_COOKIE = {"csrf_token": _CSRF}
_CSRF_HEADER = {"X-CSRF-Token": _CSRF}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def _make_user(
    session: AsyncSession,
    email: str = "acct@example.com",
    *,
    accounting_access: bool = True,
    is_platform_admin: bool = False,
    mfa_enabled: bool = False,
) -> tuple[User, str]:
    user = User(
        email=email,
        password_hash=_sha256("pw"),
        accounting_access=accounting_access,
        is_platform_admin=is_platform_admin,
        mfa_enabled=mfa_enabled or is_platform_admin,  # admin requires MFA
    )
    session.add(user)
    await session.flush()
    raw = secrets.token_urlsafe(32)
    us = UserSession(
        token_hash=_sha256(raw),
        user_id=user.id,
        expires_at=_utcnow() + timedelta(days=1),
        revoked=False,
    )
    session.add(us)
    await session.commit()
    await session.refresh(user)
    return user, raw


def _acct_cookies(raw: str) -> dict:
    return {"antcrew_session": raw, **_CSRF_COOKIE}


async def _make_receipt(session: AsyncSession, **kwargs) -> Receipt:
    from decimal import Decimal
    defaults = dict(
        vendor_name="Proveedor Test",
        date=_utcnow().date(),
        total_amount=Decimal("121.00"),
        base_imponible=Decimal("100.00"),
        tax_rate=Decimal("21.00"),
        tax_amount=Decimal("21.00"),
        vendor_country="ES",
        category="hosting",
        status="pending",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    defaults.update(kwargs)
    r = Receipt(**defaults)
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return r


async def _make_invoice(session: AsyncSession, **kwargs) -> Invoice:
    from decimal import Decimal
    defaults = dict(
        invoice_number="ANT-2025-001",
        date=_utcnow().date(),
        customer_name="Cliente Test",
        customer_country="ES",
        invoice_type="nacional",
        tax_rate=Decimal("21.00"),
        status="draft",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    defaults.update(kwargs)
    inv = Invoice(**defaults)
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return inv


# ===========================================================================
# A — Auth gates
# ===========================================================================

@pytest.mark.asyncio
async def test_a1_accounting_requires_session(client: AsyncClient):
    r = await client.get("/accounting/receipts/")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_a2_accounting_requires_access_flag(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "noaccess@example.com", accounting_access=False)
    r = await client.get("/accounting/receipts/", cookies={"antcrew_session": raw})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a3_admin_toggle_accounting_access(client: AsyncClient, session: AsyncSession):
    admin, admin_tok = await _make_user(
        session, "superadmin@example.com", is_platform_admin=True, accounting_access=False
    )
    target, _ = await _make_user(
        session, "target@example.com", accounting_access=False
    )

    r = await client.patch(
        f"/admin/users/{target.id}/accounting?accounting_access=true",
        cookies=_acct_cookies(admin_tok),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["accounting_access"] is True

    r2 = await client.patch(
        f"/admin/users/{target.id}/accounting?accounting_access=false",
        cookies=_acct_cookies(admin_tok),
        headers=_CSRF_HEADER,
    )
    assert r2.status_code == 200
    assert r2.json()["accounting_access"] is False


# ===========================================================================
# R — Receipts
# ===========================================================================

@pytest.mark.asyncio
async def test_r1_create_receipt(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r1@example.com")
    r = await client.post(
        "/accounting/receipts/",
        data={
            "vendor_name": "Anthropic",
            "date": "2025-03-15",
            "total_amount": "50.00",
            "category": "api",
            "vendor_country": "US",
        },
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["vendor_name"] == "Anthropic"
    assert body["status"] == "pending"
    assert body["vendor_country"] == "US"
    assert body["reverse_charge"] is True  # auto-set for non-ES
    assert body["vat_origin"] == "extracomunitario"


@pytest.mark.asyncio
async def test_r2_list_receipts(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r2@example.com")
    await _make_receipt(session, vendor_name="Fly.io")
    await _make_receipt(session, vendor_name="AWS", vendor_country="US")

    r = await client.get("/accounting/receipts/", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    names = [x["vendor_name"] for x in r.json()]
    assert "Fly.io" in names
    assert "AWS" in names


@pytest.mark.asyncio
async def test_r3_get_receipt_not_found(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r3@example.com")
    r = await client.get("/accounting/receipts/999999", cookies=_acct_cookies(raw))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_r4_update_receipt(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r4@example.com")
    receipt = await _make_receipt(session, vendor_name="Old Name")
    r = await client.put(
        f"/accounting/receipts/{receipt.id}",
        json={"vendor_name": "New Name", "category": "legal"},
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["vendor_name"] == "New Name"
    assert r.json()["category"] == "legal"


@pytest.mark.asyncio
async def test_r5_update_verified_receipt_blocked(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r5@example.com")
    receipt = await _make_receipt(session, status="verified")
    r = await client.put(
        f"/accounting/receipts/{receipt.id}",
        json={"vendor_name": "Hacker"},
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_r6_delete_pending_receipt(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r6@example.com")
    receipt = await _make_receipt(session)
    r = await client.delete(
        f"/accounting/receipts/{receipt.id}",
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_r7_delete_verified_receipt_blocked(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r7@example.com")
    receipt = await _make_receipt(session, status="verified")
    r = await client.delete(
        f"/accounting/receipts/{receipt.id}",
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_r8_verify_without_file_blocked(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "r8@example.com")
    receipt = await _make_receipt(session)  # no file_path
    r = await client.post(
        f"/accounting/receipts/{receipt.id}/verify",
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 409


# ===========================================================================
# I — Invoices
# ===========================================================================

@pytest.mark.asyncio
async def test_i1_create_invoice_auto_number(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i1@example.com")
    r = await client.post(
        "/accounting/invoices/",
        json={
            "date": "2025-06-01",
            "customer_name": "Acme S.L.",
            "customer_country": "ES",
            "base_imponible": 1000.0,
            "tax_rate": 21.0,
            "tax_amount": 210.0,
            "total_amount": 1210.0,
        },
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["invoice_number"] == "ANT-2025-001"
    assert body["invoice_type"] == "nacional"
    assert body["status"] == "draft"


@pytest.mark.asyncio
async def test_i2_sequential_invoice_numbers(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i2@example.com")
    payload = {
        "date": "2025-07-01",
        "customer_name": "Cliente",
        "customer_country": "ES",
        "total_amount": 100.0,
    }
    r1 = await client.post("/accounting/invoices/", json=payload,
                           cookies=_acct_cookies(raw), headers=_CSRF_HEADER)
    r2 = await client.post("/accounting/invoices/", json=payload,
                           cookies=_acct_cookies(raw), headers=_CSRF_HEADER)
    assert r1.json()["invoice_number"] == "ANT-2025-001"
    assert r2.json()["invoice_number"] == "ANT-2025-002"


@pytest.mark.asyncio
async def test_i3_invoice_type_auto_detection(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i3@example.com")

    cases = [
        ("ES", "nacional"),
        ("DE", "intracomunitaria"),   # EU member state
        ("US", "exportacion"),         # outside EU
        ("GB", "exportacion"),         # UK post-Brexit
    ]
    for country, expected_type in cases:
        r = await client.post(
            "/accounting/invoices/",
            json={
                "date": "2025-08-01",
                "customer_name": f"Client {country}",
                "customer_country": country,
                "total_amount": 100.0,
            },
            cookies=_acct_cookies(raw),
            headers=_CSRF_HEADER,
        )
        assert r.status_code == 201, f"Failed for {country}: {r.text}"
        assert r.json()["invoice_type"] == expected_type, f"Wrong type for {country}"


@pytest.mark.asyncio
async def test_i4_list_invoices(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i4@example.com")
    await _make_invoice(session, invoice_number="ANT-2025-100")
    await _make_invoice(session, invoice_number="ANT-2025-101")
    r = await client.get("/accounting/invoices/", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    numbers = [inv["invoice_number"] for inv in r.json()]
    assert "ANT-2025-100" in numbers
    assert "ANT-2025-101" in numbers


@pytest.mark.asyncio
async def test_i5_void_invoice(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i5@example.com")
    inv = await _make_invoice(session, invoice_number="ANT-2025-200")
    r = await client.post(
        f"/accounting/invoices/{inv.id}/void",
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "void"


@pytest.mark.asyncio
async def test_i6_double_void_blocked(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "i6@example.com")
    inv = await _make_invoice(session, invoice_number="ANT-2025-300", status="void")
    r = await client.post(
        f"/accounting/invoices/{inv.id}/void",
        cookies=_acct_cookies(raw),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 409


# ===========================================================================
# S — Summary (P&L)
# ===========================================================================

@pytest.mark.asyncio
async def test_s1_summary_empty(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "s1@example.com")
    r = await client.get("/accounting/summary", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["ingresos"] == 0.0
    assert body["gastos"] == 0.0
    assert body["resultado"] == 0.0


@pytest.mark.asyncio
async def test_s2_summary_aggregation(client: AsyncClient, session: AsyncSession):
    from decimal import Decimal
    _, raw = await _make_user(session, "s2@example.com")

    # 2 receipts: 100 + 50 = 150 gastos
    await _make_receipt(session, total_amount=Decimal("100.00"), base_imponible=Decimal("100.00"),
                        tax_amount=Decimal("21.00"), deductible=True)
    await _make_receipt(session, total_amount=Decimal("50.00"), base_imponible=Decimal("50.00"),
                        tax_amount=Decimal("10.50"), deductible=True)
    # 1 invoice: 1000 ingresos
    await _make_invoice(session, invoice_number="ANT-2025-500",
                        total_amount=Decimal("1000.00"), base_imponible=Decimal("826.45"),
                        tax_amount=Decimal("173.55"))

    r = await client.get("/accounting/summary", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["gastos"] == 150.0
    assert body["ingresos"] == 1000.0
    assert body["resultado"] == 850.0


# ===========================================================================
# V — VAT report (Modelo 303)
# ===========================================================================

@pytest.mark.asyncio
async def test_v1_vat_report_period_dates(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "v1@example.com")
    r = await client.get("/accounting/vat-report?year=2025&quarter=1", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "2025-Q1"
    assert body["from"] == "2025-01-01"
    assert body["to"] == "2025-03-31"


@pytest.mark.asyncio
async def test_v2_vat_report_nacional_vs_intracomunitario(client: AsyncClient, session: AsyncSession):
    from decimal import Decimal
    from datetime import date
    _, raw = await _make_user(session, "v2@example.com")

    # Nacional receipt: ES vendor
    await _make_receipt(session, vendor_country="ES",
                        date=date(2025, 1, 15),
                        base_imponible=Decimal("100.00"),
                        tax_amount=Decimal("21.00"),
                        total_amount=Decimal("121.00"),
                        deductible=True)
    # Intracom receipt: DE vendor
    await _make_receipt(session, vendor_country="DE",
                        date=date(2025, 2, 10),
                        base_imponible=Decimal("200.00"),
                        tax_amount=Decimal("42.00"),
                        total_amount=Decimal("242.00"),
                        deductible=True)

    r = await client.get("/accounting/vat-report?year=2025&quarter=1", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["iva_soportado"]["nacional"]["base"] == 100.0
    assert body["iva_soportado"]["nacional"]["cuota"] == 21.0
    assert body["iva_soportado"]["intracomunitario"]["base"] == 200.0


# ===========================================================================
# M — Modelo 349
# ===========================================================================

@pytest.mark.asyncio
async def test_m1_modelo349_empty(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "m1@example.com")
    r = await client.get("/accounting/modelo349?year=2025&quarter=2", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["total_base"] == 0.0
    assert body["operations"] == []


@pytest.mark.asyncio
async def test_m2_modelo349_only_intracomunitaria(client: AsyncClient, session: AsyncSession):
    from decimal import Decimal
    from datetime import date
    _, raw = await _make_user(session, "m2@example.com")

    await _make_invoice(session, invoice_number="ANT-2025-601",
                        date=date(2025, 4, 5),
                        customer_country="DE",
                        invoice_type="intracomunitaria",
                        customer_nif="DE123456789",
                        base_imponible=Decimal("500.00"),
                        total_amount=Decimal("500.00"))
    # Nacional invoice — must NOT appear in 349
    await _make_invoice(session, invoice_number="ANT-2025-602",
                        date=date(2025, 4, 6),
                        customer_country="ES",
                        invoice_type="nacional",
                        base_imponible=Decimal("300.00"),
                        total_amount=Decimal("363.00"))

    r = await client.get("/accounting/modelo349?year=2025&quarter=2", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert body["total_base"] == 500.0
    numbers = [op["invoice_number"] for op in body["operations"]]
    assert "ANT-2025-601" in numbers
    assert "ANT-2025-602" not in numbers


@pytest.mark.asyncio
async def test_m3_modelo349_missing_nif_alert(client: AsyncClient, session: AsyncSession):
    from decimal import Decimal
    from datetime import date
    _, raw = await _make_user(session, "m3@example.com")

    await _make_invoice(session, invoice_number="ANT-2025-700",
                        date=date(2025, 7, 1),
                        customer_country="FR",
                        invoice_type="intracomunitaria",
                        customer_nif=None,   # missing NIF
                        base_imponible=Decimal("1000.00"),
                        total_amount=Decimal("1000.00"))

    r = await client.get("/accounting/modelo349?year=2025&quarter=3", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    body = r.json()
    assert len(body["alerts"]) > 0
    assert body["operations"][0]["missing_nif"] is True


# ===========================================================================
# E — Export ZIP
# ===========================================================================

@pytest.mark.asyncio
async def test_e1_export_zip_content_type(client: AsyncClient, session: AsyncSession):
    _, raw = await _make_user(session, "e1@example.com")
    r = await client.get("/accounting/export", cookies=_acct_cookies(raw))
    assert r.status_code == 200
    assert "application/zip" in r.headers["content-type"]


# ===========================================================================
# B — Billing profile
# ===========================================================================

@pytest.mark.asyncio
async def test_b1_billing_profile_empty(client: AsyncClient, session: AsyncSession):
    ws = Workspace(name="BillingWS", slug="billing-ws")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/billing-profile")
    assert r.status_code == 200
    body = r.json()
    assert body["complete"] is False
    assert body["billing_country"] == "ES"


@pytest.mark.asyncio
async def test_b2_billing_profile_save_sets_complete(client: AsyncClient, session: AsyncSession):
    ws = Workspace(name="BillingWS2", slug="billing-ws-2")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(
        f"/workspaces/{ws.id}/billing-profile",
        json={
            "billing_entity_type": "autonomo",
            "billing_razon_social": "Iago Pueyo",
            "billing_nif": "12345678z",
            "billing_address": "Calle Mayor 1",
            "billing_city": "Madrid",
            "billing_country": "ES",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["complete"] is True
    assert body["billing_nif"] == "12345678Z"  # uppercased
    assert body["billing_razon_social"] == "Iago Pueyo"


@pytest.mark.asyncio
async def test_b3_billing_profile_invalid_entity_type(client: AsyncClient, session: AsyncSession):
    ws = Workspace(name="BillingWS3", slug="billing-ws-3")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(
        f"/workspaces/{ws.id}/billing-profile",
        json={"billing_entity_type": "cooperativa"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_b4_billing_profile_nif_uppercased(client: AsyncClient, session: AsyncSession):
    ws = Workspace(name="BillingWS4", slug="billing-ws-4")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(
        f"/workspaces/{ws.id}/billing-profile",
        json={"billing_nif": "b12345678"},
    )
    assert r.status_code == 200
    assert r.json()["billing_nif"] == "B12345678"
