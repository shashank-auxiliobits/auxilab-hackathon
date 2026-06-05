"""Unit tests for the Vendor Name Normaliser."""

from __future__ import annotations

from ap_invoice.schemas.tools import VendorMasterEntry, VendorNormaliseRequest
from ap_invoice.services.vendor_normaliser import normalise_vendor

MASTER = [
    VendorMasterEntry(
        id="v1", canonical_name="Microsoft Corporation", aliases=["MSFT", "Microsoft"]
    ),
    VendorMasterEntry(id="v2", canonical_name="Amazon Web Services", aliases=["AWS"]),
]


def test_alias_match() -> None:
    r = normalise_vendor(VendorNormaliseRequest(raw_name="MSFT Corp.", vendor_master=MASTER))
    assert r.is_recognized
    assert r.match is not None
    assert r.match.canonical_name == "Microsoft Corporation"
    assert r.match.match_type == "alias"
    assert not r.recommend_onboarding


def test_exact_match_ignoring_suffix() -> None:
    r = normalise_vendor(
        VendorNormaliseRequest(raw_name="Microsoft Corporation Ltd", vendor_master=MASTER)
    )
    assert r.is_recognized
    assert r.match is not None
    assert r.match.canonical_name == "Microsoft Corporation"


def test_fuzzy_match() -> None:
    r = normalise_vendor(
        VendorNormaliseRequest(raw_name="Amazon Web Service", vendor_master=MASTER, threshold=80)
    )
    assert r.is_recognized
    assert r.match is not None
    assert r.match.canonical_name == "Amazon Web Services"


def test_unrecognised_flags_onboarding() -> None:
    r = normalise_vendor(VendorNormaliseRequest(raw_name="Globex Industries", vendor_master=MASTER))
    assert not r.is_recognized
    assert r.match is None
    assert r.recommend_onboarding


def test_empty_master() -> None:
    r = normalise_vendor(VendorNormaliseRequest(raw_name="Anything", vendor_master=[]))
    assert not r.is_recognized
    assert r.recommend_onboarding
