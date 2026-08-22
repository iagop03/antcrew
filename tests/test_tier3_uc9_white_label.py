"""Tests for UC9: WhiteLabelWrapper + BillingRecord."""
from __future__ import annotations

import pytest
from antcrew.agency import BillingRecord, WhiteLabelWrapper
from antcrew.core.events import EventBus, WebhookSink


# ---------------------------------------------------------------------------
# BillingRecord
# ---------------------------------------------------------------------------

def test_billing_record_fields():
    rec = BillingRecord(
        client_label="acme",
        cost_usd=1.00,
        billed_usd=4.00,
        margin_usd=3.00,
        margin_pct=75.0,
        run_id="run-1",
        state={"_run_id": "run-1"},
    )
    assert rec.client_label == "acme"
    assert rec.cost_usd == 1.00
    assert rec.billed_usd == 4.00
    assert rec.margin_usd == 3.00
    assert rec.margin_pct == 75.0
    assert rec.run_id == "run-1"


def test_billing_record_none_margin_pct():
    rec = BillingRecord(
        client_label="x",
        cost_usd=0.0,
        billed_usd=0.0,
        margin_usd=0.0,
        margin_pct=None,
        run_id="",
        state={},
    )
    assert rec.margin_pct is None


def test_billing_record_state_forwarded():
    state = {"_run_id": "r99", "content_piece": None}
    rec = BillingRecord(
        client_label="client",
        cost_usd=0.5,
        billed_usd=2.0,
        margin_usd=1.5,
        margin_pct=75.0,
        run_id="r99",
        state=state,
    )
    assert rec.state["_run_id"] == "r99"


# ---------------------------------------------------------------------------
# WhiteLabelWrapper — validation
# ---------------------------------------------------------------------------

def _make_team(cost: float = 1.0, run_id: str = "r"):
    """Minimal fake team that returns a RunResult-like object."""
    from types import SimpleNamespace
    return SimpleNamespace(
        run=lambda request, **kw: SimpleNamespace(
            cost_usd=cost,
            state={"_run_id": run_id},
        )
    )


def test_wrapper_requires_non_empty_client_label():
    team = _make_team()
    with pytest.raises(ValueError, match="client_label"):
        WhiteLabelWrapper(team, client_label="")


def test_wrapper_requires_stripped_non_empty_label():
    team = _make_team()
    with pytest.raises(ValueError, match="client_label"):
        WhiteLabelWrapper(team, client_label="   ")


def test_wrapper_rejects_negative_markup():
    team = _make_team()
    with pytest.raises(ValueError, match="markup_pct"):
        WhiteLabelWrapper(team, client_label="x", markup_pct=-1)


def test_wrapper_accepts_zero_markup():
    team = _make_team(cost=2.0)
    w = WhiteLabelWrapper(team, client_label="x", markup_pct=0)
    rec = w.run("task")
    assert rec.billed_usd == pytest.approx(2.0)
    assert rec.margin_usd == pytest.approx(0.0)
    assert rec.margin_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# WhiteLabelWrapper — billing math
# ---------------------------------------------------------------------------

def test_markup_300_default():
    """Default 300 % markup: billed = cost × 4, margin = cost × 3, margin_pct = 75 %."""
    team = _make_team(cost=1.0)
    w = WhiteLabelWrapper(team, client_label="acme")
    rec = w.run("do something")
    assert rec.billed_usd == pytest.approx(4.0)
    assert rec.margin_usd == pytest.approx(3.0)
    assert rec.margin_pct == pytest.approx(75.0)


def test_markup_100():
    """100 % markup: billed = cost × 2, margin = 50 %."""
    team = _make_team(cost=1.0)
    w = WhiteLabelWrapper(team, client_label="client", markup_pct=100)
    rec = w.run("task")
    assert rec.billed_usd == pytest.approx(2.0)
    assert rec.margin_usd == pytest.approx(1.0)
    assert rec.margin_pct == pytest.approx(50.0)


def test_markup_200():
    """200 % markup: billed = cost × 3, margin = 66.67 %."""
    team = _make_team(cost=3.0)
    w = WhiteLabelWrapper(team, client_label="client", markup_pct=200)
    rec = w.run("task")
    assert rec.billed_usd == pytest.approx(9.0)
    assert rec.margin_usd == pytest.approx(6.0)
    assert rec.margin_pct == pytest.approx(66.67, rel=1e-2)


def test_markup_zero_cost_margin_pct_none():
    """When cost and billed are 0, margin_pct must be None (avoid ZeroDivisionError)."""
    team = _make_team(cost=0.0)
    w = WhiteLabelWrapper(team, client_label="client", markup_pct=300)
    rec = w.run("task")
    assert rec.billed_usd == pytest.approx(0.0)
    assert rec.margin_usd == pytest.approx(0.0)
    assert rec.margin_pct is None


def test_markup_fractional_cost():
    team = _make_team(cost=0.042)
    w = WhiteLabelWrapper(team, client_label="x", markup_pct=300)
    rec = w.run("task")
    assert rec.billed_usd == pytest.approx(0.042 * 4, rel=1e-5)
    assert rec.margin_usd == pytest.approx(0.042 * 3, rel=1e-5)


# ---------------------------------------------------------------------------
# WhiteLabelWrapper — run_id + state pass-through
# ---------------------------------------------------------------------------

def test_run_id_forwarded():
    team = _make_team(run_id="my-run-42")
    w = WhiteLabelWrapper(team, client_label="acme")
    rec = w.run("task")
    assert rec.run_id == "my-run-42"


def test_state_forwarded():
    from types import SimpleNamespace
    inner_state = {"_run_id": "r1", "content_piece": "hello"}
    fake_team = SimpleNamespace(
        run=lambda req, **kw: SimpleNamespace(cost_usd=0.1, state=inner_state)
    )
    w = WhiteLabelWrapper(fake_team, client_label="client")
    rec = w.run("task")
    assert rec.state is inner_state


def test_client_label_stored():
    team = _make_team()
    w = WhiteLabelWrapper(team, client_label="my-client", markup_pct=100)
    rec = w.run("task")
    assert rec.client_label == "my-client"


def test_client_label_is_stripped():
    team = _make_team()
    w = WhiteLabelWrapper(team, client_label="  acme  ", markup_pct=100)
    assert w.client_label == "acme"


# ---------------------------------------------------------------------------
# WhiteLabelWrapper — event emission
# ---------------------------------------------------------------------------

def test_events_emitted():
    """agency.run_start and agency.run_end events must be emitted."""
    from antcrew.core.events import EventBus, WebhookSink

    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("agency.run_start", sink.handle)
    local_bus.subscribe("agency.run_end", sink.handle)

    from antcrew import agency as _agency_module
    import antcrew.agency as _mod
    original_bus = _mod.bus
    _mod.bus = local_bus
    try:
        team = _make_team(cost=1.0, run_id="r")
        w = WhiteLabelWrapper(team, client_label="test-client")
        w.run("task")
    finally:
        _mod.bus = original_bus

    drained = sink.drain()
    event_types = [d[0] for d in drained]
    assert "agency.run_start" in event_types
    assert "agency.run_end" in event_types


def test_run_end_event_contains_margin():
    """agency.run_end payload must include margin_usd."""
    from antcrew.core.events import EventBus, WebhookSink
    import antcrew.agency as _mod

    local_bus = EventBus()
    sink = WebhookSink()
    local_bus.subscribe("agency.run_end", sink.handle)

    original_bus = _mod.bus
    _mod.bus = local_bus
    try:
        team = _make_team(cost=1.0)
        w = WhiteLabelWrapper(team, client_label="acme")
        w.run("task")
    finally:
        _mod.bus = original_bus

    payload = sink.drain()[0][1]
    assert "margin_usd" in payload
    assert "billed_usd" in payload
    assert "client_label" in payload


# ---------------------------------------------------------------------------
# WhiteLabelWrapper — kwargs forwarding
# ---------------------------------------------------------------------------

def test_kwargs_forwarded_to_team():
    """Extra kwargs passed to w.run() should be forwarded to team.run()."""
    received = {}

    from types import SimpleNamespace
    def fake_run(request, **kw):
        received.update(kw)
        return SimpleNamespace(cost_usd=0.0, state={})

    fake_team = SimpleNamespace(run=fake_run)
    w = WhiteLabelWrapper(fake_team, client_label="x", markup_pct=0)
    w.run("task", thread_id="my-thread")
    assert received.get("thread_id") == "my-thread"
