"""Vendor and vendor-policy schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import EmailStr, Field, field_validator

from ap_invoice.core.enums import VendorStatus
from ap_invoice.schemas.common import APIModel, ORMModel


class VendorPolicyBase(APIModel):
    payment_terms: str = Field(default="Net 30", max_length=64)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    allow_early_payment_discount: bool = True
    mandatory_fields: list[str] = Field(
        default_factory=lambda: [
            "invoice_number",
            "invoice_date",
            "vendor_name",
            "grand_total",
        ]
    )
    min_completeness_score: Decimal = Field(default=Decimal("100.00"), ge=0, le=100)
    auto_approve_max_amount: Decimal | None = Field(default=None, ge=0)
    requires_review_above_amount: Decimal | None = Field(default=None, ge=0)
    amount_tolerance_pct: Decimal = Field(default=Decimal("5.00"), ge=0, le=100)
    duplicate_lookback_days: int = Field(default=90, ge=1, le=3650)
    terms_and_conditions: dict[str, Any] = Field(default_factory=dict)
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


class VendorPolicyCreate(VendorPolicyBase):
    """Payload to create a new policy version for a vendor."""


class VendorPolicyRead(ORMModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    version: int
    is_active: bool
    payment_terms: str
    currency: str
    allow_early_payment_discount: bool
    mandatory_fields: list[str]
    min_completeness_score: Decimal
    auto_approve_max_amount: Decimal | None
    requires_review_above_amount: Decimal | None
    amount_tolerance_pct: Decimal
    duplicate_lookback_days: int
    terms_and_conditions: dict[str, Any]
    effective_from: date | None
    effective_to: date | None
    created_at: datetime
    updated_at: datetime


class VendorCreate(APIModel):
    canonical_name: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    tax_id: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    status: VendorStatus = VendorStatus.ACTIVE
    notes: str | None = None
    # Optionally create an initial policy in the same request.
    policy: VendorPolicyCreate | None = None


class VendorUpdate(APIModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    aliases: list[str] | None = None
    tax_id: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    status: VendorStatus | None = None
    notes: str | None = None


class VendorRead(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    canonical_name: str
    display_name: str | None
    aliases: list[str]
    tax_id: str | None
    email: str | None
    status: VendorStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


class VendorWithPolicy(VendorRead):
    active_policy: VendorPolicyRead | None = None
