"""Tests for structured_feedback flow: ReviewDecision → HitlReview → agent."""
import json
import pytest
from sqlmodel import select

from app.models.review import HitlReview
from app.models.run import Run


pytestmark = pytest.mark.anyio


async def _create_run(client, session) -> str:
    ws_id = 1
    run = Run(
        run_id="run-sf-test",
        team="DevTeam",
        request="Test",
        thread_id="t-sf",
        status="running",
        workspace_id=ws_id,
    )
    session.add(run)
    await session.commit()
    return run.run_id


async def _create_review(client, run_id: str, *, schema: dict | None = None, session=None) -> str:
    r = await client.post("/reviews/", json={
        "run_id": run_id,
        "agent_name": "test_agent",
        "review_type": "approval",
    })
    assert r.status_code == 201, r.text
    review_id = r.json()["review_id"]

    if schema is not None and session is not None:
        row = (await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))).first()
        row.feedback_schema_json = json.dumps(schema)
        session.add(row)
        await session.commit()

    return review_id


async def test_structured_feedback_stored(client, session):
    """structured_feedback in ReviewDecision is persisted to structured_feedback_json."""
    run_id = await _create_run(client, session)
    schema = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
    }
    review_id = await _create_review(client, run_id, schema=schema, session=session)

    resp = await client.post(
        f"/reviews/{review_id}",
        json={"decision": "approve", "structured_feedback": {"approved": True}},
    )
    assert resp.status_code == 200, resp.text

    await session.refresh((await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first())
    row = (await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))).first()
    assert row.structured_feedback_json is not None
    assert json.loads(row.structured_feedback_json) == {"approved": True}


async def test_structured_feedback_without_schema_accepted(client, session):
    """structured_feedback stored as-is when no schema declared."""
    run_id = await _create_run(client, session)
    review_id = await _create_review(client, run_id)

    resp = await client.post(
        f"/reviews/{review_id}",
        json={"decision": "reject", "structured_feedback": {"reason": "scope too broad"}},
    )
    assert resp.status_code == 200, resp.text


async def test_structured_feedback_schema_validation_rejects_invalid(client, session):
    """structured_feedback that violates declared schema → 422 (when jsonschema installed)."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        pytest.skip("jsonschema not installed")

    run_id = await _create_run(client, session)
    schema = {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
    }
    review_id = await _create_review(client, run_id, schema=schema, session=session)

    resp = await client.post(
        f"/reviews/{review_id}",
        json={"decision": "approve", "structured_feedback": {"approved": "not-a-bool"}},
    )
    assert resp.status_code == 422


async def test_decision_without_structured_feedback_unchanged(client, session):
    """Omitting structured_feedback leaves structured_feedback_json = None."""
    run_id = await _create_run(client, session)
    review_id = await _create_review(client, run_id)

    resp = await client.post(f"/reviews/{review_id}", json={"decision": "approve"})
    assert resp.status_code == 200

    row = (await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))).first()
    assert row.structured_feedback_json is None
