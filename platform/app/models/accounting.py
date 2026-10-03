"""Accounting models: Receipt (gastos) and Invoice (ingresos)."""
from __future__ import annotations

import json
from datetime import date as _date
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow

# ISO 3166-1 alpha-2 EU member states (for IVA intracomunitario detection)
_EU_COUNTRIES = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "FI", "FR",
    "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
    "PL", "PT", "RO", "SE", "SI", "SK",
})


def vat_origin(country: str) -> str:
    """Classify vendor country as 'nacional', 'intracomunitario', or 'extracomunitario'."""
    if country == "ES":
        return "nacional"
    if country in _EU_COUNTRIES:
        return "intracomunitario"
    return "extracomunitario"


def invoice_operation_type(country: str) -> str:
    """Classify invoice operation type for outbound invoices.

    nacional      — client in Spain (IVA applies)
    intracomunitaria — client in another EU member state with VAT number (IVA 0%, Modelo 349)
    exportacion   — client outside EU (IVA 0%, no 349)
    """
    cc = country.upper()
    if cc == "ES":
        return "nacional"
    if cc in _EU_COUNTRIES:
        return "intracomunitaria"
    return "exportacion"


class Receipt(SQLModel, table=True):
    """Factura recibida / gasto. All Hacienda-required fields included."""

    __tablename__ = "receipt"

    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Factura identity ──────────────────────────────────────────────────────
    invoice_number: Optional[str] = Field(default=None)        # Nº factura del proveedor
    date: _date = Field(...)                                    # Fecha de emisión

    # ── Proveedor ─────────────────────────────────────────────────────────────
    vendor_name: str
    vendor_nif: Optional[str] = Field(default=None)            # NIF / CIF / VAT number
    vendor_country: str = Field(default="ES")                  # ISO 3166-1 alpha-2
    vendor_address: Optional[str] = Field(default=None)

    # ── Importes ──────────────────────────────────────────────────────────────
    base_imponible: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)
    tax_rate: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=5)
    tax_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)
    total_amount: Decimal = Field(decimal_places=2, max_digits=10)
    currency: str = Field(default="EUR")
    eur_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)

    # ── IVA flags ─────────────────────────────────────────────────────────────
    reverse_charge: bool = Field(default=False)                # Inversión del sujeto pasivo
    doc_type: str = Field(default="factura_completa")          # factura_completa | simplificada | receipt

    # ── Clasificación ─────────────────────────────────────────────────────────
    category: str = Field(default="other")                     # hosting|domain|api|software|personnel|legal|other
    deductible: bool = Field(default=True)

    # ── Adjunto ───────────────────────────────────────────────────────────────
    file_path: Optional[str] = Field(default=None)
    filename: Optional[str] = Field(default=None)

    # ── Estado y meta ─────────────────────────────────────────────────────────
    status: str = Field(default="pending")                     # pending | verified | rejected
    description: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def vat_origin(self) -> str:
        return vat_origin(self.vendor_country)

    def effective_eur(self) -> float:
        """EUR amount to use for accounting (eur_amount if set, otherwise total_amount)."""
        v = self.eur_amount if self.eur_amount is not None else self.total_amount
        return float(v)

    def effective_base(self) -> float:
        return float(self.base_imponible) if self.base_imponible is not None else 0.0

    def effective_tax(self) -> float:
        return float(self.tax_amount) if self.tax_amount is not None else 0.0


class Invoice(SQLModel, table=True):
    """Factura emitida / ingreso. Numeración correlativa ANT-YYYY-NNN."""

    __tablename__ = "invoice"

    id: Optional[int] = Field(default=None, primary_key=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    invoice_number: str = Field(unique=True)                   # ANT-2025-001
    date: _date = Field(...)
    due_date: Optional[_date] = Field(default=None)

    # ── Cliente ───────────────────────────────────────────────────────────────
    customer_name: str
    customer_nif: Optional[str] = Field(default=None)
    customer_country: str = Field(default="ES")
    customer_address: Optional[str] = Field(default=None)

    # ── Line items & importes ─────────────────────────────────────────────────
    line_items: str = Field(default="[]")                      # JSON: [{description, qty, unit_price, tax_rate}]
    base_imponible: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)
    tax_rate: Optional[Decimal] = Field(default=Decimal("21.00"), decimal_places=2, max_digits=5)
    tax_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)
    total_amount: Optional[Decimal] = Field(default=None, decimal_places=2, max_digits=10)
    currency: str = Field(default="EUR")

    # ── Tipo de operación ─────────────────────────────────────────────────────
    invoice_type: str = Field(default="nacional")              # nacional | intracomunitaria | exportacion

    # ── Estado ────────────────────────────────────────────────────────────────
    status: str = Field(default="draft")                       # draft | issued | paid | void
    payment_date: Optional[_date] = Field(default=None)
    payment_method: Optional[str] = Field(default=None)

    # ── Meta ──────────────────────────────────────────────────────────────────
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def parsed_line_items(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.line_items)
        except Exception:
            return []
