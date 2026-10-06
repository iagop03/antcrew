"""Accounting API — receipts (gastos), invoices (ingresos), P&L summary, VAT report, export.

All endpoints require is_platform_admin=True and accounting_access=True on the authenticated User.
Files are stored on local disk under RECEIPTS_DIR (default ./data/receipts/).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import os
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.admin_auth import get_session_user
from app.core.database import get_session
from app.models.accounting import (
    _EU_COUNTRIES,
    Invoice,
    Receipt,
    invoice_operation_type,
    vat_origin,
)
from app.models.auth import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/accounting", tags=["accounting"])

_RECEIPTS_DIR = Path(os.environ.get("RECEIPTS_DIR", "./data/receipts"))
_ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
_MAX_UPLOAD_MB = 20

_CATEGORIES = ("hosting", "domain", "api", "software", "personnel", "legal", "marketing", "other")
_DOC_TYPES = ("factura_completa", "simplificada", "receipt")
_STATUSES = ("pending", "verified", "rejected")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_dir() -> Path:
    _RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    return _RECEIPTS_DIR


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_accounting(
    user: User = Depends(get_session_user),
) -> User:
    if not user.accounting_access:
        raise HTTPException(403, "Accounting access required")
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(v: Optional[Decimal]) -> Optional[float]:
    return float(v) if v is not None else None


def _receipt_dict(r: Receipt) -> dict[str, Any]:
    return {
        "id": r.id,
        "invoice_number": r.invoice_number,
        "date": r.date.isoformat() if r.date else None,
        "vendor_name": r.vendor_name,
        "vendor_nif": r.vendor_nif,
        "vendor_country": r.vendor_country,
        "vendor_address": r.vendor_address,
        "base_imponible": _d(r.base_imponible),
        "tax_rate": _d(r.tax_rate),
        "tax_amount": _d(r.tax_amount),
        "total_amount": _d(r.total_amount),
        "currency": r.currency,
        "eur_amount": _d(r.eur_amount),
        "reverse_charge": r.reverse_charge,
        "doc_type": r.doc_type,
        "category": r.category,
        "deductible": r.deductible,
        "filename": r.filename,
        "has_file": bool(r.file_path),
        "status": r.status,
        "description": r.description,
        "notes": r.notes,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "vat_origin": r.vat_origin(),
    }


def _invoice_dict(inv: Invoice) -> dict[str, Any]:
    return {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "date": inv.date.isoformat() if inv.date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "customer_name": inv.customer_name,
        "customer_nif": inv.customer_nif,
        "customer_country": inv.customer_country,
        "customer_address": inv.customer_address,
        "line_items": inv.parsed_line_items(),
        "base_imponible": _d(inv.base_imponible),
        "tax_rate": _d(inv.tax_rate),
        "tax_amount": _d(inv.tax_amount),
        "total_amount": _d(inv.total_amount),
        "currency": inv.currency,
        "invoice_type": inv.invoice_type,
        "status": inv.status,
        "payment_date": inv.payment_date.isoformat() if inv.payment_date else None,
        "payment_method": inv.payment_method,
        "notes": inv.notes,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


async def _next_invoice_number(session: AsyncSession, year: int) -> str:
    prefix = f"ANT-{year}-"
    rows = (await session.exec(
        select(Invoice.invoice_number)
        .where(Invoice.invoice_number.startswith(prefix))  # type: ignore[attr-defined]
        .order_by(Invoice.invoice_number.desc())           # type: ignore[attr-defined]
    )).all()
    if not rows:
        return f"{prefix}001"
    last = rows[0]
    try:
        n = int(last.split("-")[-1])
    except (ValueError, IndexError):
        n = 0
    return f"{prefix}{n + 1:03d}"


def _parse_decimal(v: Optional[str]) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _parse_date(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Receipt CRUD
# ---------------------------------------------------------------------------

@router.get("/receipts/")
async def list_receipts(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    q = select(Receipt).order_by(Receipt.date.desc())  # type: ignore[attr-defined]
    if from_date:
        d = _parse_date(from_date)
        if d:
            q = q.where(Receipt.date >= d)
    if to_date:
        d = _parse_date(to_date)
        if d:
            q = q.where(Receipt.date <= d)
    if category:
        q = q.where(Receipt.category == category)
    if status:
        q = q.where(Receipt.status == status)
    if country:
        q = q.where(Receipt.vendor_country == country)
    rows = (await session.exec(q)).all()
    return [_receipt_dict(r) for r in rows]


@router.post("/receipts/", status_code=201)
async def create_receipt(
    # File
    file: Optional[UploadFile] = File(None),
    # Required fields
    vendor_name: str = Form(...),
    date_str: str = Form(..., alias="date"),
    total_amount: str = Form(...),
    # Optional fields
    invoice_number: Optional[str] = Form(None),
    vendor_nif: Optional[str] = Form(None),
    vendor_country: str = Form("ES"),
    vendor_address: Optional[str] = Form(None),
    base_imponible: Optional[str] = Form(None),
    tax_rate: Optional[str] = Form(None),
    tax_amount: Optional[str] = Form(None),
    currency: str = Form("EUR"),
    eur_amount: Optional[str] = Form(None),
    doc_type: str = Form("factura_completa"),
    category: str = Form("other"),
    description: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    deductible: bool = Form(True),
    user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    receipt_date = _parse_date(date_str)
    if not receipt_date:
        raise HTTPException(422, "Invalid date format — use YYYY-MM-DD")

    # Auto-detect reverse_charge when country ≠ ES
    auto_reverse = vendor_country != "ES"

    # Handle file upload
    file_path: Optional[str] = None
    filename: Optional[str] = None
    if file and file.filename:
        content = await file.read()
        if len(content) > _MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"File too large (max {_MAX_UPLOAD_MB} MB)")
        mime = file.content_type or mimetypes.guess_type(file.filename)[0] or ""
        if mime not in _ALLOWED_MIME:
            raise HTTPException(415, "Only PDF, JPEG, PNG, WEBP accepted")
        ext = Path(file.filename).suffix
        stored_name = f"{uuid.uuid4()}{ext}"
        dest = _ensure_dir() / stored_name
        dest.write_bytes(content)
        file_path = str(dest)
        filename = file.filename

    row = Receipt(
        invoice_number=invoice_number or None,
        date=receipt_date,
        vendor_name=vendor_name,
        vendor_nif=vendor_nif or None,
        vendor_country=vendor_country.upper(),
        vendor_address=vendor_address or None,
        base_imponible=_parse_decimal(base_imponible),
        tax_rate=_parse_decimal(tax_rate),
        tax_amount=_parse_decimal(tax_amount),
        total_amount=Decimal(total_amount),
        currency=currency.upper(),
        eur_amount=_parse_decimal(eur_amount),
        reverse_charge=auto_reverse,
        doc_type=doc_type,
        category=category,
        deductible=deductible,
        file_path=file_path,
        filename=filename,
        status="pending",
        description=description or None,
        notes=notes or None,
        created_by=user.email or user.display_name,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _receipt_dict(row)


@router.get("/receipts/{receipt_id}")
async def get_receipt(
    receipt_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Receipt, receipt_id)
    if not row:
        raise HTTPException(404, "Receipt not found")
    return _receipt_dict(row)


@router.put("/receipts/{receipt_id}")
async def update_receipt(
    receipt_id: int,
    body: dict,
    user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Receipt, receipt_id)
    if not row:
        raise HTTPException(404, "Receipt not found")
    if row.status == "verified":
        raise HTTPException(409, "Verified receipts cannot be edited")

    editable = (
        "invoice_number", "vendor_name", "vendor_nif", "vendor_country", "vendor_address",
        "base_imponible", "tax_rate", "tax_amount", "total_amount", "currency", "eur_amount",
        "reverse_charge", "doc_type", "category", "deductible", "description", "notes",
    )
    for field in editable:
        if field in body:
            v = body[field]
            if field in ("base_imponible", "tax_rate", "tax_amount", "total_amount", "eur_amount"):
                v = _parse_decimal(str(v)) if v is not None else None
            setattr(row, field, v)
    if "date" in body:
        row.date = _parse_date(body["date"]) or row.date

    row.updated_at = _utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _receipt_dict(row)


@router.delete("/receipts/{receipt_id}", status_code=204, response_model=None)
async def delete_receipt(
    receipt_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(Receipt, receipt_id)
    if not row:
        raise HTTPException(404, "Receipt not found")
    if row.status == "verified":
        raise HTTPException(409, "Verified receipts cannot be deleted")
    if row.file_path:
        try:
            Path(row.file_path).unlink(missing_ok=True)
        except Exception:
            pass
    await session.delete(row)
    await session.commit()


@router.post("/receipts/{receipt_id}/verify")
async def verify_receipt(
    receipt_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(Receipt, receipt_id)
    if not row:
        raise HTTPException(404, "Receipt not found")
    if not row.file_path or not Path(row.file_path).exists():
        raise HTTPException(409, "Cannot verify receipt without attached file")
    row.status = "verified"
    row.updated_at = _utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _receipt_dict(row)


@router.get("/receipts/{receipt_id}/file")
async def download_receipt_file(
    receipt_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(Receipt, receipt_id)
    if not row or not row.file_path:
        raise HTTPException(404, "File not found")
    p = Path(row.file_path)
    if not p.exists():
        raise HTTPException(404, "File not found on disk")
    mime = mimetypes.guess_type(row.filename or p.name)[0] or "application/octet-stream"
    return StreamingResponse(
        p.open("rb"),
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{row.filename or p.name}"'},
    )


# ---------------------------------------------------------------------------
# Invoice CRUD
# ---------------------------------------------------------------------------

_INVOICE_TYPES = ("nacional", "intracomunitaria", "exportacion")


class InvoiceCreate(BaseModel):
    date: str
    due_date: Optional[str] = None
    customer_name: str
    customer_nif: Optional[str] = None
    customer_country: str = "ES"
    customer_address: Optional[str] = None
    line_items: list[dict] = []
    base_imponible: Optional[float] = None
    tax_rate: float = 21.0
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: str = "EUR"
    invoice_type: Optional[str] = None  # auto-computed from country if None
    notes: Optional[str] = None


class InvoicePatch(BaseModel):
    date: Optional[str] = None
    due_date: Optional[str] = None
    customer_name: Optional[str] = None
    customer_nif: Optional[str] = None
    customer_country: Optional[str] = None
    customer_address: Optional[str] = None
    line_items: Optional[list[dict]] = None
    base_imponible: Optional[float] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    invoice_type: Optional[str] = None
    status: Optional[str] = None
    payment_date: Optional[str] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None


@router.get("/invoices/")
async def list_invoices(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    status: Optional[str] = Query(None),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    q = select(Invoice).order_by(Invoice.date.desc())  # type: ignore[attr-defined]
    if from_date:
        d = _parse_date(from_date)
        if d:
            q = q.where(Invoice.date >= d)
    if to_date:
        d = _parse_date(to_date)
        if d:
            q = q.where(Invoice.date <= d)
    if status:
        q = q.where(Invoice.status == status)
    rows = (await session.exec(q)).all()
    return [_invoice_dict(inv) for inv in rows]


@router.post("/invoices/", status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    invoice_date = _parse_date(body.date)
    if not invoice_date:
        raise HTTPException(422, "Invalid date")
    year = invoice_date.year
    number = await _next_invoice_number(session, year)

    country = body.customer_country.upper()
    op_type = body.invoice_type if body.invoice_type in _INVOICE_TYPES else invoice_operation_type(country)

    inv = Invoice(
        invoice_number=number,
        date=invoice_date,
        due_date=_parse_date(body.due_date),
        customer_name=body.customer_name,
        customer_nif=body.customer_nif,
        customer_country=country,
        customer_address=body.customer_address,
        line_items=json.dumps(body.line_items),
        base_imponible=Decimal(str(body.base_imponible)) if body.base_imponible is not None else None,
        tax_rate=Decimal(str(body.tax_rate)),
        tax_amount=Decimal(str(body.tax_amount)) if body.tax_amount is not None else None,
        total_amount=Decimal(str(body.total_amount)) if body.total_amount is not None else None,
        currency=body.currency.upper(),
        invoice_type=op_type,
        status="draft",
        notes=body.notes,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return _invoice_dict(inv)


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    inv = await session.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return _invoice_dict(inv)


@router.put("/invoices/{invoice_id}")
async def update_invoice(
    invoice_id: int,
    body: InvoicePatch,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    inv = await session.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status == "void":
        raise HTTPException(409, "Voided invoices cannot be edited")
    if inv.status not in ("draft",) and body.status is None:
        pass  # allow status transitions via the status field

    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        if k in ("date", "due_date", "payment_date"):
            v = _parse_date(v)
        elif k in ("base_imponible", "tax_rate", "tax_amount", "total_amount"):
            v = Decimal(str(v)) if v is not None else None
        elif k == "line_items":
            v = json.dumps(v)
        elif k == "customer_country":
            v = v.upper()
        setattr(inv, k, v)
    inv.updated_at = _utcnow()
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return _invoice_dict(inv)


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(
    invoice_id: int,
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    inv = await session.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status == "void":
        raise HTTPException(409, "Already voided")
    inv.status = "void"
    inv.updated_at = _utcnow()
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return _invoice_dict(inv)


# ---------------------------------------------------------------------------
# Summary (P&L)
# ---------------------------------------------------------------------------

@router.get("/summary")
async def get_summary(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    d_from = _parse_date(from_date) if from_date else None
    d_to = _parse_date(to_date) if to_date else None

    # Receipts (gastos)
    rq = select(Receipt)
    if d_from:
        rq = rq.where(Receipt.date >= d_from)
    if d_to:
        rq = rq.where(Receipt.date <= d_to)
    receipts = (await session.exec(rq)).all()

    total_gastos = sum(r.effective_eur() for r in receipts if r.deductible)
    total_base_soportada = sum(r.effective_base() for r in receipts if r.deductible)
    total_iva_soportado = sum(r.effective_tax() for r in receipts if r.deductible)
    pending_receipts = sum(1 for r in receipts if r.status == "pending")
    unconfirmed_isp = sum(1 for r in receipts if r.reverse_charge and r.status == "pending")

    by_category: dict[str, float] = {}
    for r in receipts:
        if r.deductible:
            by_category[r.category] = by_category.get(r.category, 0.0) + r.effective_eur()

    # Monthly breakdown for chart
    monthly: dict[str, dict] = {}
    for r in receipts:
        if r.deductible and r.date:
            key = r.date.strftime("%Y-%m")
            monthly.setdefault(key, {"gastos": 0.0, "ingresos": 0.0})
            monthly[key]["gastos"] += r.effective_eur()

    # Invoices (ingresos)
    iq = select(Invoice).where(Invoice.status != "void")
    if d_from:
        iq = iq.where(Invoice.date >= d_from)
    if d_to:
        iq = iq.where(Invoice.date <= d_to)
    invoices = (await session.exec(iq)).all()

    total_ingresos = sum(float(inv.total_amount or 0) for inv in invoices)
    total_base_repercutida = sum(float(inv.base_imponible or 0) for inv in invoices)
    total_iva_repercutido = sum(float(inv.tax_amount or 0) for inv in invoices)
    for inv in invoices:
        if inv.date:
            key = inv.date.strftime("%Y-%m")
            monthly.setdefault(key, {"gastos": 0.0, "ingresos": 0.0})
            monthly[key]["ingresos"] += float(inv.total_amount or 0)

    iva_a_liquidar = total_iva_repercutido - total_iva_soportado
    resultado = total_ingresos - total_gastos

    return {
        "ingresos": round(total_ingresos, 2),
        "gastos": round(total_gastos, 2),
        "resultado": round(resultado, 2),
        "base_repercutida": round(total_base_repercutida, 2),
        "iva_repercutido": round(total_iva_repercutido, 2),
        "base_soportada": round(total_base_soportada, 2),
        "iva_soportado": round(total_iva_soportado, 2),
        "iva_a_liquidar": round(iva_a_liquidar, 2),
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])},
        "monthly": dict(sorted(monthly.items())),
        "alerts": {
            "pending_receipts": pending_receipts,
            "unconfirmed_isp": unconfirmed_isp,
        },
    }


# ---------------------------------------------------------------------------
# VAT report (datos Modelo 303)
# ---------------------------------------------------------------------------

@router.get("/vat-report")
async def vat_report(
    year: int = Query(...),
    quarter: int = Query(..., ge=1, le=4),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    q_start_month = (quarter - 1) * 3 + 1
    d_from = date(year, q_start_month, 1)
    end_month = q_start_month + 2
    end_year = year
    if end_month == 12:
        d_to = date(end_year, 12, 31)
    else:
        import calendar
        _, last_day = calendar.monthrange(end_year, end_month)
        d_to = date(end_year, end_month, last_day)

    receipts = (await session.exec(
        select(Receipt).where(Receipt.date >= d_from).where(Receipt.date <= d_to)
    )).all()

    invoices = (await session.exec(
        select(Invoice)
        .where(Invoice.date >= d_from)
        .where(Invoice.date <= d_to)
        .where(Invoice.status != "void")
    )).all()

    # IVA repercutido (ingresos)
    base_repercutida = sum(float(inv.base_imponible or 0) for inv in invoices)
    cuota_repercutida = sum(float(inv.tax_amount or 0) for inv in invoices)

    # IVA soportado deducible — por origen
    nacional = [r for r in receipts if r.deductible and r.vat_origin() == "nacional"]
    intracom = [r for r in receipts if r.deductible and r.vat_origin() == "intracomunitario"]
    extracom = [r for r in receipts if r.deductible and r.vat_origin() == "extracomunitario"]
    isp_rows = [r for r in receipts if r.deductible and r.reverse_charge]

    base_nacional = sum(r.effective_base() for r in nacional)
    cuota_nacional = sum(r.effective_tax() for r in nacional)
    base_intracom = sum(r.effective_base() for r in intracom)
    cuota_intracom = sum(r.effective_tax() for r in intracom)
    base_extracom = sum(r.effective_eur() for r in extracom)
    base_isp = sum(r.effective_base() for r in isp_rows)
    cuota_isp = round(base_isp * 0.21, 2)  # autorepercutida al 21%

    total_iva_soportado = cuota_nacional + cuota_intracom + cuota_isp
    resultado = round(cuota_repercutida - total_iva_soportado, 2)

    return {
        "period": f"{year}-Q{quarter}",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "iva_repercutido": {
            "casillas": "01-09",
            "base": round(base_repercutida, 2),
            "cuota": round(cuota_repercutida, 2),
        },
        "iva_soportado": {
            "nacional": {
                "casillas": "28-36",
                "base": round(base_nacional, 2),
                "cuota": round(cuota_nacional, 2),
                "receipts": len(nacional),
            },
            "intracomunitario": {
                "casillas": "38-39",
                "base": round(base_intracom, 2),
                "cuota": round(cuota_intracom, 2),
                "receipts": len(intracom),
            },
            "extracomunitario": {
                "casillas": "40-41",
                "base": round(base_extracom, 2),
                "receipts": len(extracom),
            },
        },
        "inversion_sujeto_pasivo": {
            "casillas": "12-13",
            "base": round(base_isp, 2),
            "cuota_autorepercutida": round(cuota_isp, 2),
            "receipts": len(isp_rows),
            "note": "IVA al 21% autorepercutido. Incluir también en casilla 36 (soportado deducible).",
        },
        "resultado": {
            "casilla": "64",
            "a_ingresar": round(resultado, 2) if resultado >= 0 else 0,
            "a_compensar": round(-resultado, 2) if resultado < 0 else 0,
        },
        "alerts": {
            "unverified_receipts": sum(1 for r in receipts if r.status == "pending"),
            "missing_nif": sum(1 for r in receipts if not r.vendor_nif),
        },
    }


# ---------------------------------------------------------------------------
# Modelo 349 — Declaración recapitulativa de operaciones intracomunitarias
# ---------------------------------------------------------------------------

@router.get("/modelo349")
async def modelo349(
    year: int = Query(...),
    quarter: int = Query(..., ge=1, le=4),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> dict:
    import calendar as _cal

    q_start_month = (quarter - 1) * 3 + 1
    d_from = date(year, q_start_month, 1)
    end_month = q_start_month + 2
    _, last_day = _cal.monthrange(year, end_month)
    d_to = date(year, end_month, last_day)

    invoices = (await session.exec(
        select(Invoice)
        .where(Invoice.invoice_type == "intracomunitaria")
        .where(Invoice.date >= d_from)
        .where(Invoice.date <= d_to)
        .where(Invoice.status != "void")
        .order_by(Invoice.date)  # type: ignore[attr-defined]
    )).all()

    total_base = sum(float(inv.base_imponible or 0) for inv in invoices)

    by_country: dict[str, float] = {}
    for inv in invoices:
        cc = inv.customer_country
        by_country[cc] = by_country.get(cc, 0.0) + float(inv.base_imponible or 0)

    alerts = []
    missing_nif = [inv for inv in invoices if not inv.customer_nif]
    if missing_nif:
        alerts.append(
            f"{len(missing_nif)} factura(s) sin NIF-IVA del cliente — "
            "obligatorio para el Modelo 349."
        )

    return {
        "period": f"{year}-Q{quarter}",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "total_base": round(total_base, 2),
        "operations": [
            {
                "invoice_number": inv.invoice_number,
                "date": inv.date.isoformat() if inv.date else None,
                "customer_name": inv.customer_name,
                "customer_nif": inv.customer_nif or "",
                "customer_country": inv.customer_country,
                "base_imponible": float(inv.base_imponible or 0),
                "total_amount": float(inv.total_amount or 0),
                "missing_nif": not bool(inv.customer_nif),
            }
            for inv in invoices
        ],
        "by_country": {k: round(v, 2) for k, v in sorted(by_country.items(), key=lambda x: -x[1])},
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Export ZIP
# ---------------------------------------------------------------------------

@router.get("/export")
async def export_zip(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    _user: User = Depends(require_accounting),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    d_from = _parse_date(from_date) if from_date else None
    d_to = _parse_date(to_date) if to_date else None

    # Load data
    rq = select(Receipt).order_by(Receipt.date)  # type: ignore[attr-defined]
    if d_from:
        rq = rq.where(Receipt.date >= d_from)
    if d_to:
        rq = rq.where(Receipt.date <= d_to)
    receipts = (await session.exec(rq)).all()

    iq = select(Invoice).where(Invoice.status != "void").order_by(Invoice.date)  # type: ignore[attr-defined]
    if d_from:
        iq = iq.where(Invoice.date >= d_from)
    if d_to:
        iq = iq.where(Invoice.date <= d_to)
    invoices = (await session.exec(iq)).all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── libro_facturas_recibidas.csv ───────────────────────────────────────
        csv_buf = io.StringIO()
        w = csv.writer(csv_buf)
        w.writerow([
            "Fecha", "Nº Factura Proveedor", "Proveedor", "NIF/VAT Proveedor",
            "País", "Base Imponible EUR", "Tipo IVA %", "Cuota IVA EUR", "Total EUR",
            "Moneda Original", "Total Moneda Original", "Tipo Documento",
            "Inversión Sujeto Pasivo", "Origen IVA", "Categoría", "Deducible",
            "Estado", "Descripción", "Adjunto",
        ])
        for r in receipts:
            w.writerow([
                r.date.isoformat() if r.date else "",
                r.invoice_number or "",
                r.vendor_name,
                r.vendor_nif or "",
                r.vendor_country,
                _d(r.base_imponible) or "",
                _d(r.tax_rate) or "",
                _d(r.tax_amount) or "",
                r.effective_eur(),
                r.currency,
                _d(r.total_amount) or "",
                r.doc_type,
                "Sí" if r.reverse_charge else "No",
                r.vat_origin(),
                r.category,
                "Sí" if r.deductible else "No",
                r.status,
                r.description or "",
                r.filename or "",
            ])
        zf.writestr("libro_facturas_recibidas.csv", csv_buf.getvalue())

        # ── libro_facturas_emitidas.csv ────────────────────────────────────────
        csv_buf2 = io.StringIO()
        w2 = csv.writer(csv_buf2)
        w2.writerow([
            "Nº Factura", "Fecha Emisión", "Fecha Vencimiento", "Cliente",
            "NIF Cliente", "País Cliente", "Tipo Operación", "Base Imponible", "Tipo IVA %",
            "Cuota IVA", "Total", "Moneda", "Estado", "Fecha Cobro", "Método Pago",
        ])
        for inv in invoices:
            w2.writerow([
                inv.invoice_number,
                inv.date.isoformat() if inv.date else "",
                inv.due_date.isoformat() if inv.due_date else "",
                inv.customer_name,
                inv.customer_nif or "",
                inv.customer_country,
                inv.invoice_type,
                _d(inv.base_imponible) or "",
                _d(inv.tax_rate) or "",
                _d(inv.tax_amount) or "",
                _d(inv.total_amount) or "",
                inv.currency,
                inv.status,
                inv.payment_date.isoformat() if inv.payment_date else "",
                inv.payment_method or "",
            ])
        zf.writestr("libro_facturas_emitidas.csv", csv_buf2.getvalue())

        # ── adjuntos/recibidas/ ────────────────────────────────────────────────
        for r in receipts:
            if r.file_path and Path(r.file_path).exists():
                ext = Path(r.file_path).suffix
                safe_vendor = "".join(c if c.isalnum() else "_" for c in r.vendor_name)[:30]
                arc_name = f"adjuntos/recibidas/{r.date.isoformat()}_{safe_vendor}_{r.invoice_number or r.id}{ext}"
                zf.write(r.file_path, arc_name)

    period_label = f"{from_date or 'inicio'}_{to_date or 'hoy'}"
    filename = f"contabilidad_{period_label}.zip"
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
