"""#5331 — the byte-based storage cap MECHANISM (owner ruling 2026-09-26).

The ruling: *"we have to migrate away from counting nodes and start counting
in mb/gb for storage"*. This suite pins the MECHANISM and its one load-bearing
property — **ABSENT = NOT ENFORCED** — and deliberately sets NO byte allowance:
the values are the owner's, not this code's.

What is being pinned:
  1. A byte dimension (`max_storage_bytes`) resolved through the SAME
     precedence as the node cap — per-org override first, pricing tier second.
  2. With no allowance configured anywhere, the node cap is the enforced one
     and the byte seam is never even consulted.
  3. The byte reading is supplied by the CALLER through a callable seam, so
     the cap never imports the graph byte meter (#5331 sibling).
  4. A configured-but-unmeasurable allowance fails CLOSED — a cap that cannot
     be measured must not silently pass (#686).

Every one of these is mutation-checked (see the test docstrings).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tortoise.pricing as pricing
from tortoise.quota import (
    QuotaCheckError,
    QuotaExceededError,
    StorageBytes,
    enforce_org_limit,
    quota_refusal_payload,
    resolve_org_limits,
    resolve_storage_allowance,
    storage_allowance,
)


@pytest.fixture(autouse=True)
def _fresh_pricing():
    pricing.reload()
    yield
    pricing.reload()


@pytest.fixture(autouse=True)
def _embedded_env(monkeypatch, tmp_path):
    """Route quota SDKs to an embedded temp DB (mirrors tests/test_quota.py)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "quota.db"))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _forbidden_reading():
    """A reading that proves the byte seam was NOT consulted.

    If the byte branch is ever entered while no allowance is configured, this
    raises — so a regression that makes the cap byte-enforced unconditionally
    turns red on the *assertion*, not merely on a changed status code.
    """
    raise AssertionError(
        "the storage reading must NOT be invoked while no byte allowance is "
        "configured (#5331 absent = not enforced)")


def _sdk_with_points(tmp_path, n):
    from tortoise.sdk import TortoiseSDK
    db = os.path.join(tmp_path, f"team_{os.urandom(4).hex()}.db")
    sdk = TortoiseSDK(db, namespace=f"n_{os.urandom(4).hex()}")
    for i in range(n):
        sdk.create_point("statement", f"S{i}")
    return sdk


def _find_org_id(sdk) -> str:
    rows = sdk._get_registry().query(
        "MATCH (t:Team) RETURN t.id LIMIT 1").result_set
    assert rows, "no team provisioned"
    return rows[0][0]


@pytest.fixture
def reg_sdk(monkeypatch, tmp_path):
    from tortoise.sdk import TortoiseSDK
    db = os.path.join(tmp_path, "quota.db")
    sdk = TortoiseSDK(db, namespace="registry")
    sdk.org_create(name="storage-cap-team")
    yield sdk
    sdk.close()


# ── the pricing dimension is OPTIONAL and absent by default ────────────────


class TestPricingDimension:
    def test_real_pricing_configures_no_byte_allowance(self):
        """The shipped pricing.json sets NO byte allowance — if it did, the
        migration would have been switched on by a value nobody ratified."""
        for tier in pricing.all_tiers():
            assert pricing.tier_limits(tier)["max_storage_bytes"] is None, (
                f"pricing.json tier {tier!r} carries a byte allowance — the "
                "value is the owner's to set, not the code's (#5331)")

    def test_tier_allowance_is_read_from_pricing(self, tmp_path, monkeypatch):
        """Adding the key to pricing.json is the WHOLE owner action (no code
        change) — prove the loader actually reads it."""
        data = json.loads(Path(pricing._PRICING_PATH).read_text())
        data["tiers"]["free"]["max_storage_bytes"] = 5_000_000
        p = tmp_path / "pricing.json"
        p.write_text(json.dumps(data))
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(p))
        pricing.reload()
        assert pricing.tier_limits("free")["max_storage_bytes"] == 5_000_000
        # A key is NOT required on every tier — _REQUIRED_LIMIT_KEYS untouched,
        # so a tier without it still loads (absent = not enforced).
        assert pricing.tier_limits("solo")["max_storage_bytes"] is None


# ── precedence: per-org override first, tier second ────────────────────────


class TestResolution:
    def test_absent_everywhere_is_none(self):
        assert resolve_storage_allowance(None, None) is None

    def test_tier_value_used_when_no_per_org_override(self):
        assert resolve_storage_allowance(None, 123) == 123

    def test_per_org_override_wins_over_tier(self):
        assert resolve_storage_allowance(1, 123) == 1

    def test_storage_allowance_reads_per_org_key_from_limits(self):
        assert storage_allowance(
            {"org_id": "o", "max_storage_bytes": 42}) == 42

    def test_storage_allowance_falls_back_to_tier_key(self):
        assert storage_allowance(
            {"org_id": "o", "tier": "free"}) is None  # shipped pricing

    def test_storage_allowance_without_tier_is_none(self):
        assert storage_allowance({"org_id": "o"}) is None

    def test_registry_resolution_reads_per_org_property(self, reg_sdk):
        """The registry Team node is a per-org override store — same slot as
        t.max_points. Absent → None (NOT ENFORCED), then set → honoured."""
        tid = _find_org_id(reg_sdk)
        assert resolve_org_limits(tid)["max_storage_bytes"] is None
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_storage_bytes = $v",
            params={"id": tid, "v": 12345},
        )
        assert resolve_org_limits(tid)["max_storage_bytes"] == 12345


# ── THE load-bearing property: absent = enforcement unchanged ──────────────


class TestNoByteAllowanceEnforcementUnchanged:
    """With no byte allowance configured, the node cap is the enforced one.

    Mutation: make the byte dimension enforce unconditionally (treat an absent
    allowance as 0, or drop the `is not None` guard) — both tests below turn
    red, the first on the assertion in `_forbidden_reading` and the second
    because a `QuotaExceededError` on "points" becomes a fail-closed
    `QuotaCheckError` (or a "storage" refusal).
    """

    def test_over_node_cap_still_refuses_as_a_points_cap(self, tmp_path):
        sdk = _sdk_with_points(tmp_path, 1)
        limits = {"org_id": "org1", "tier": "free", "max_points": 1}
        with pytest.raises(QuotaExceededError) as ei:
            enforce_org_limit(limits, "points", sdk=sdk,
                              storage_reading=_forbidden_reading)
        assert ei.value.resource == "points"
        assert (ei.value.used, ei.value.limit) == (1, 1)
        sdk.close()

    def test_under_node_cap_still_admits(self, tmp_path):
        sdk = _sdk_with_points(tmp_path, 1)
        limits = {"org_id": "org1", "tier": "free", "max_points": 10}
        enforce_org_limit(limits, "points", sdk=sdk,
                          storage_reading=_forbidden_reading)
        sdk.close()

    def test_absent_allowance_is_not_treated_as_zero(self):
        """The mutated form most likely to slip in: `or 0`. An org with no
        allowance must not be refused by a 0-byte cap."""
        limits = {"org_id": "org1", "tier": "free", "max_points": None}
        assert storage_allowance(limits) is None
        enforce_org_limit(limits, "points",
                          storage_reading=_forbidden_reading)  # must not raise


# ── configured byte allowance: the migration path ──────────────────────────


class TestConfiguredByteAllowance:
    def test_over_ceiling_refuses_as_a_storage_cap(self):
        limits = {"org_id": "org1", "max_storage_bytes": 1000}
        with pytest.raises(QuotaExceededError) as ei:
            enforce_org_limit(
                limits, "points",
                storage_reading=lambda: StorageBytes(
                    used=1001, estimated=True, note="SAMPLING ESTIMATE"))
        assert ei.value.resource == "storage"
        assert (ei.value.used, ei.value.limit) == (1001, 1000)
        # The instrument's honesty rides the refusal (#5331 honesty rule).
        assert ei.value.estimated is True
        payload = quota_refusal_payload(ei.value)
        assert payload["estimated"] is True
        assert "SAMPLING ESTIMATE" in payload["message"]

    def test_at_ceiling_refuses(self):
        """`used >= limit` — the node cap's boundary semantics, preserved.
        Mutation: flip to `used > limit` and THIS test turns red (1000 is the
        equality case); the over-ceiling sibling uses 1001 and is unaffected."""
        limits = {"org_id": "org1", "max_storage_bytes": 1000}
        with pytest.raises(QuotaExceededError):
            enforce_org_limit(limits, "points",
                              storage_reading=lambda: StorageBytes(used=1000))

    def test_under_ceiling_admits(self):
        limits = {"org_id": "org1", "max_storage_bytes": 1000}
        enforce_org_limit(limits, "points",
                          storage_reading=lambda: StorageBytes(used=999))

    def test_byte_allowance_replaces_the_node_cap(self, tmp_path):
        """The point of the migration: when bytes are configured, the node
        count is NO LONGER the cap. 5 nodes vs max_points=1 would 402 on the
        node path; bytes under the ceiling must admit."""
        sdk = _sdk_with_points(tmp_path, 5)
        limits = {"org_id": "org1", "max_points": 1,
                  "max_storage_bytes": 10_000_000}
        enforce_org_limit(limits, "points", sdk=sdk,
                          storage_reading=lambda: StorageBytes(used=1))
        sdk.close()

    def test_tier_configured_allowance_enforces_on_points(self, tmp_path,
                                                          monkeypatch):
        """A tier-only allowance (no per-org override, no explicit key in the
        limits dict) must still enforce — otherwise a pricing.json edit would
        silently do nothing on the REST lane."""
        data = json.loads(Path(pricing._PRICING_PATH).read_text())
        data["tiers"]["free"]["max_storage_bytes"] = 5_000_000
        p = tmp_path / "pricing.json"
        p.write_text(json.dumps(data))
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(p))
        pricing.reload()
        limits = {"org_id": "org1", "tier": "free"}
        with pytest.raises(QuotaExceededError) as ei:
            enforce_org_limit(
                limits, "points",
                storage_reading=lambda: StorageBytes(used=6_000_000))
        assert ei.value.resource == "storage" and ei.value.limit == 5_000_000

    def test_per_org_override_binds_over_tier(self, tmp_path, monkeypatch):
        data = json.loads(Path(pricing._PRICING_PATH).read_text())
        data["tiers"]["free"]["max_storage_bytes"] = 5_000_000
        p = tmp_path / "pricing.json"
        p.write_text(json.dumps(data))
        monkeypatch.setattr(pricing, "_PRICING_PATH", str(p))
        pricing.reload()
        limits = {"org_id": "org1", "tier": "free", "max_storage_bytes": 1}
        with pytest.raises(QuotaExceededError) as ei:
            enforce_org_limit(limits, "points",
                              storage_reading=lambda: StorageBytes(used=2))
        assert ei.value.limit == 1


class TestConfiguredButUnmeasurableFailsClosed:
    """A configured cap that cannot be measured must not silently pass.

    Mutation: replace the `storage_reading is None` / `reading is None` /
    exception branches with a `return` — each corresponding test turns red.
    """

    def test_no_reading_supplied_fails_closed(self):
        with pytest.raises(QuotaCheckError, match="no storage reading"):
            enforce_org_limit({"org_id": "o", "max_storage_bytes": 10},
                              "points")

    def test_unavailable_reading_fails_closed(self):
        with pytest.raises(QuotaCheckError, match="unavailable"):
            enforce_org_limit({"org_id": "o", "max_storage_bytes": 10},
                              "points", storage_reading=lambda: None)

    def test_raising_reading_fails_closed(self):
        def _boom():
            raise RuntimeError("engine down")
        with pytest.raises(QuotaCheckError, match="reading failed"):
            enforce_org_limit({"org_id": "o", "max_storage_bytes": 10},
                              "points", storage_reading=_boom)

    def test_malformed_allowance_fails_closed(self):
        with pytest.raises(QuotaCheckError, match="invalid"):
            enforce_org_limit({"org_id": "o", "max_storage_bytes": "lots"},
                              "points",
                              storage_reading=lambda: StorageBytes(used=1))


# ── the byte dimension is a `points` dimension, not a global one ───────────


class TestDimensionScope:
    def test_hosted_org_limits_builder_carries_the_byte_dimension(self):
        """hosted_api's node-sync resolver (the mint gate's and the session
        lane's source) must carry `max_storage_bytes`, or a per-org override
        would be silently dropped on those lanes (review finding #3, #5331)."""
        from tortoise.hosted_api import _org_limits_from_node
        lim = _org_limits_from_node(
            {"id": "o1", "tier": "free", "max_storage_bytes": 777})
        assert lim["max_storage_bytes"] == 777
        assert _org_limits_from_node(
            {"id": "o2", "tier": "free"})["max_storage_bytes"] is None

    def test_non_points_resource_is_untouched_by_a_byte_allowance(self, reg_sdk):
        """`api_keys` must still be gated on max_api_keys even with a byte
        allowance present — the byte dimension replaces the NODE cap only.
        Mutation: move the byte branch above the `resource == "points"` guard
        and this raises on `_forbidden_reading` instead of a quota refusal."""
        tid = _find_org_id(reg_sdk)
        limits = {"org_id": tid, "tier": "free", "max_api_keys": 0,
                  "max_storage_bytes": 1}
        with pytest.raises(QuotaExceededError) as ei:
            enforce_org_limit(limits, "api_keys", sdk=reg_sdk,
                              storage_reading=_forbidden_reading)
        assert ei.value.resource == "api_keys"
