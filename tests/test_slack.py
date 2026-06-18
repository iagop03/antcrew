"""Tests for SlackChannel — mocks slack_sdk and slack_bolt, no real credentials."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from antcrew.core.artifacts import (
    PRD, Ticket, Priority, TicketStatus,
    CodeArtifact, CodeReview, ReviewFinding,
    ResearchDocument, ResearchSection, ContentPiece,
)
from antcrew.integrations.slack import (
    _artifact_text,
    _review_blocks,
    _feedback_modal,
    _edit_modal,
    _serialize_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prd(**kw):
    defaults = dict(title="My PRD", summary="s", goals=[], out_of_scope=[],
                    functional_requirements=["req1", "req2"], non_functional_requirements=[], open_questions=[])
    defaults.update(kw)
    return PRD(**defaults)


def _mock_slack_sdk():
    """Context managers that mock both slack_sdk and slack_bolt."""
    sdk_mock = MagicMock()
    bolt_mock = MagicMock()
    bolt_instance = MagicMock()
    bolt_mock.return_value = bolt_instance
    handler_mock = MagicMock()
    socket_handler_mock = MagicMock()
    socket_handler_mock.return_value = handler_mock
    return sdk_mock, bolt_mock, socket_handler_mock, bolt_instance


# ---------------------------------------------------------------------------
# _artifact_text
# ---------------------------------------------------------------------------

class TestArtifactText:
    def test_prd(self):
        text = _artifact_text(_prd())
        assert "My PRD" in text
        assert "req1" in text

    def test_tickets(self):
        tickets = [Ticket(id="T-001", title="Auth", description="d",
                          priority=Priority.HIGH, acceptance_criteria=[], dependencies=[],
                          status=TicketStatus.OPEN)]
        text = _artifact_text(tickets)
        assert "T-001" in text
        assert "Auth" in text

    def test_code_artifacts(self):
        arts = [CodeArtifact(ticket_id="T-001", file_path="src/app.py",
                             description="Main", language="python", content="pass")]
        text = _artifact_text(arts)
        assert "src/app.py" in text

    def test_code_review(self):
        review = CodeReview(
            verdict="approve", summary="Looks good",
            findings=[ReviewFinding(severity="info", file_path="f.py",
                                    message="ok", suggestion="", line=None)]
        )
        text = _artifact_text(review)
        assert "APPROVE" in text
        assert "Looks good" in text

    def test_research_document(self):
        doc = ResearchDocument(
            title="AI Risks", topic="AI",
            key_findings=["finding A", "finding B"],
            sections=[ResearchSection(heading="H", content="c")],
            sources=[],
        )
        text = _artifact_text(doc)
        assert "AI Risks" in text
        assert "finding A" in text

    def test_content_piece_with_body(self):
        piece = ContentPiece(
            title="Blog Post", target_audience="devs", tone="professional",
            outline=[], body="Introduction paragraph."
        )
        text = _artifact_text(piece)
        assert "Blog Post" in text
        assert "Introduction" in text

    def test_none_artifact(self):
        assert "No artifact" in _artifact_text(None)

    def test_long_text_truncated(self):
        piece = ContentPiece(
            title="T", target_audience="a", tone="p", outline=[],
            body="x" * 600
        )
        text = _artifact_text(piece)
        assert len(text) <= 3000
        assert "…" in text


# ---------------------------------------------------------------------------
# _review_blocks
# ---------------------------------------------------------------------------

class TestReviewBlocks:
    def test_structure(self):
        blocks = _review_blocks(_prd(), "business_analyst", "ac_sess1")
        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "section" in types
        assert "actions" in types

    def test_action_ids_contain_base(self):
        blocks = _review_blocks(_prd(), "ba", "ac_sess1")
        actions_block = next(b for b in blocks if b["type"] == "actions")
        action_ids = [e["action_id"] for e in actions_block["elements"]]
        assert "ac_sess1_approve" in action_ids
        assert "ac_sess1_reject" in action_ids
        assert "ac_sess1_feedback" in action_ids
        assert "ac_sess1_edit" in action_ids

    def test_header_contains_agent_name(self):
        blocks = _review_blocks(_prd(), "pm_agent", "ac_x")
        header = next(b for b in blocks if b["type"] == "header")
        assert "pm_agent" in header["text"]["text"]

    def test_text_section_truncated_at_2900(self):
        piece = ContentPiece(
            title="T", target_audience="a", tone="p", outline=[],
            body="y" * 5000
        )
        blocks = _review_blocks(piece, "editor", "ac_s")
        section = next(b for b in blocks if b["type"] == "section")
        assert len(section["text"]["text"]) <= 2900


# ---------------------------------------------------------------------------
# _feedback_modal and _edit_modal
# ---------------------------------------------------------------------------

class TestModals:
    def test_feedback_modal_structure(self):
        modal = _feedback_modal("ac_sess1_fb")
        assert modal["type"] == "modal"
        assert modal["callback_id"] == "ac_sess1_fb"
        assert modal["blocks"][0]["block_id"] == "fb_block"

    def test_edit_modal_structure(self):
        modal = _edit_modal("ac_sess1_edit", '{"key": "val"}')
        assert modal["type"] == "modal"
        assert modal["callback_id"] == "ac_sess1_edit"
        assert '{"key": "val"}' in modal["blocks"][0]["element"]["initial_value"]

    def test_edit_modal_truncates_long_json(self):
        long_json = json.dumps({"data": "x" * 3000})
        modal = _edit_modal("cb", long_json)
        initial = modal["blocks"][0]["element"]["initial_value"]
        assert len(initial) <= 2900


# ---------------------------------------------------------------------------
# _serialize_artifact
# ---------------------------------------------------------------------------

class TestSerializeArtifact:
    def test_pydantic_model(self):
        prd = _prd()
        result = _serialize_artifact(prd)
        data = json.loads(result)
        assert data["title"] == "My PRD"

    def test_list_of_pydantic(self):
        arts = [CodeArtifact(ticket_id="T-001", file_path="f.py",
                             description="d", language="python", content="x")]
        result = _serialize_artifact(arts)
        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["file_path"] == "f.py"

    def test_none(self):
        assert _serialize_artifact(None) == "{}"


# ---------------------------------------------------------------------------
# SlackChannel instantiation (with mocked deps)
# ---------------------------------------------------------------------------

class TestSlackChannelInstantiation:
    def test_raises_without_sdk(self):
        with patch("antcrew.integrations.slack._SLACK_SDK", False), \
             patch("antcrew.integrations.slack._SLACK_BOLT", True):
            from antcrew.integrations.slack import SlackChannel
            with pytest.raises(ImportError, match="slack-sdk"):
                SlackChannel(bot_token="xoxb-x", channel_id="C1", app_token="xapp-x")

    def test_raises_without_bolt(self):
        with patch("antcrew.integrations.slack._SLACK_SDK", True), \
             patch("antcrew.integrations.slack._SLACK_BOLT", False), \
             patch("antcrew.integrations.slack.WebClient"):
            from antcrew.integrations.slack import SlackChannel
            with pytest.raises(ImportError, match="slack-bolt"):
                SlackChannel(bot_token="xoxb-x", channel_id="C1", app_token="xapp-x")

    def test_instantiates_with_explicit_args(self):
        with patch("antcrew.integrations.slack._SLACK_SDK", True), \
             patch("antcrew.integrations.slack._SLACK_BOLT", True), \
             patch("antcrew.integrations.slack.WebClient") as wc:
            from antcrew.integrations.slack import SlackChannel
            ch = SlackChannel(bot_token="xoxb-t", channel_id="C999", app_token="xapp-t")
            assert ch._channel_id == "C999"
            wc.assert_called_once_with(token="xoxb-t")

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C-env")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env")
        with patch("antcrew.integrations.slack._SLACK_SDK", True), \
             patch("antcrew.integrations.slack._SLACK_BOLT", True), \
             patch("antcrew.integrations.slack.WebClient"):
            from antcrew.integrations.slack import SlackChannel
            ch = SlackChannel()
            assert ch._bot_token == "xoxb-env"
            assert ch._channel_id == "C-env"


# ---------------------------------------------------------------------------
# SlackChannel.notify() and send_for_review()
# ---------------------------------------------------------------------------

def _make_channel():
    with patch("antcrew.integrations.slack._SLACK_SDK", True), \
         patch("antcrew.integrations.slack._SLACK_BOLT", True), \
         patch("antcrew.integrations.slack.WebClient") as wc:
        from antcrew.integrations.slack import SlackChannel
        ch = SlackChannel(bot_token="xoxb-t", channel_id="C1", app_token="xapp-t")
        ch._client = wc.return_value
        return ch, wc.return_value


@pytest.mark.asyncio
async def test_notify_calls_chat_post_message():
    ch, mock_client = _make_channel()
    mock_client.chat_postMessage = MagicMock(return_value={"ok": True})
    await ch.notify("Pipeline started")
    mock_client.chat_postMessage.assert_called_once()
    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert call_kwargs["channel"] == "C1"
    assert "Pipeline started" in call_kwargs["text"]


def _make_post_side_effect(session_id: str, result: dict):
    """
    Returns a side-effect function for chat_postMessage.

    When called from run_in_executor's thread pool, it uses
    run_coroutine_threadsafe to safely deliver the result to the
    asyncio queue owned by the event-loop thread.
    """
    def _post(**kwargs):
        from antcrew.integrations.slack import SlackChannel as _SC
        loop = _SC._loop
        q    = _SC._queues.get(session_id)
        if loop and q:
            asyncio.run_coroutine_threadsafe(q.put(result), loop)
        return {"ok": True}
    return _post


@pytest.mark.asyncio
async def test_send_for_review_approve():
    from antcrew.integrations.slack import SlackChannel

    ch, _ = _make_channel()
    SlackChannel._queues    = {}
    SlackChannel._loop      = asyncio.get_event_loop()
    ch._client.chat_postMessage = MagicMock(
        side_effect=_make_post_side_effect(
            "sess-a", {"decision": "approve", "edited": None, "feedback": None}
        )
    )
    with patch.object(ch, "_ensure_bolt_running"):
        result = await ch.send_for_review(_prd(), "ba", "sess-a")
    assert result["decision"] == "approve"


@pytest.mark.asyncio
async def test_send_for_review_feedback():
    from antcrew.integrations.slack import SlackChannel

    ch, _ = _make_channel()
    SlackChannel._queues = {}
    SlackChannel._loop   = asyncio.get_event_loop()
    ch._client.chat_postMessage = MagicMock(
        side_effect=_make_post_side_effect(
            "sess-fb", {"decision": "feedback", "feedback": "Add more details", "edited": None}
        )
    )
    with patch.object(ch, "_ensure_bolt_running"):
        result = await ch.send_for_review(_prd(), "ba", "sess-fb")
    assert result["decision"] == "feedback"
    assert result["feedback"] == "Add more details"


@pytest.mark.asyncio
async def test_send_for_review_edit():
    from antcrew.integrations.slack import SlackChannel

    ch, _ = _make_channel()
    SlackChannel._queues = {}
    SlackChannel._loop   = asyncio.get_event_loop()
    edited_json = '{"title": "Edited PRD", "summary": "s", "goals": []}'
    ch._client.chat_postMessage = MagicMock(
        side_effect=_make_post_side_effect(
            "sess-edit", {"decision": "edit", "edited": edited_json, "feedback": None}
        )
    )
    with patch.object(ch, "_ensure_bolt_running"):
        result = await ch.send_for_review(_prd(), "ba", "sess-edit")
    assert result["decision"] == "edit"
    assert result["edited"] == edited_json


# ---------------------------------------------------------------------------
# SlackChannel._put (thread-safe resolver)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_delivers_to_queue():
    from antcrew.integrations.slack import SlackChannel

    loop = asyncio.get_event_loop()
    SlackChannel._loop = loop
    q: asyncio.Queue = asyncio.Queue()
    SlackChannel._queues["test-put"] = q

    SlackChannel._put("test-put", {"decision": "reject", "edited": None, "feedback": None})
    result = await asyncio.wait_for(q.get(), timeout=1.0)
    assert result["decision"] == "reject"


def test_put_logs_warning_for_missing_session():
    from antcrew.integrations.slack import SlackChannel
    import logging

    SlackChannel._queues = {}
    SlackChannel._loop = asyncio.new_event_loop()

    with patch.object(logging.getLogger("antcrew.integrations.slack"), "warning") as mock_warn:
        SlackChannel._put("nonexistent-session", {"decision": "approve"})
        mock_warn.assert_called_once()
