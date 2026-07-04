"""Tests for JiraIntegration."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from antcrew.core.artifacts import Priority, Ticket, TicketStatus
from antcrew.integrations.jira import (
    JiraIntegration,
    _adf_doc,
    _PRIORITY_MAP,
    _antcrew_label,
    _ANTCREW_LABEL_PREFIX,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ticket(
    id_: str = "TICKET-03CE2BBF",
    title: str = "Implement auth",
    description: str = "Add JWT auth",
    priority: Priority = Priority.HIGH,
    acs: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=id_,
        title=title,
        description=description,
        priority=priority,
        status=TicketStatus.OPEN,
        acceptance_criteria=acs or ["Test passes", "Reviewed"],
        dependencies=[],
    )


def _jira(project_key: str = "DEV") -> JiraIntegration:
    return JiraIntegration(
        url="https://test.atlassian.net",
        email="test@test.com",
        api_token="secret",
        project_key=project_key,
    )


def _mock_post(return_key: str = "DEV-1"):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"key": return_key}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _mock_get_empty():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"issues": []}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _mock_get_found(key: str = "DEV-1"):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"issues": [{"key": key, "fields": {"summary": "x"}}]}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# _antcrew_label helper
# ---------------------------------------------------------------------------

def test_antcrew_label_format():
    assert _antcrew_label("TICKET-03CE2BBF") == "antcrew:TICKET-03CE2BBF"


def test_antcrew_label_prefix_constant():
    assert _ANTCREW_LABEL_PREFIX == "antcrew:"


# ---------------------------------------------------------------------------
# _adf_doc helper
# ---------------------------------------------------------------------------

def test_adf_doc_no_criteria():
    doc = _adf_doc("some text", [])
    assert doc["type"] == "doc"
    assert len(doc["content"]) == 1
    assert doc["content"][0]["type"] == "paragraph"


def test_adf_doc_with_criteria():
    doc = _adf_doc("text", ["AC1", "AC2"])
    types = [block["type"] for block in doc["content"]]
    assert "heading" in types
    assert "bulletList" in types
    bullet_items = [b for b in doc["content"] if b["type"] == "bulletList"][0]["content"]
    assert len(bullet_items) == 2


# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("priority,expected", [
    (Priority.LOW,      "Low"),
    (Priority.MEDIUM,   "Medium"),
    (Priority.HIGH,     "High"),
    (Priority.CRITICAL, "Highest"),
])
def test_priority_map(priority, expected):
    assert _PRIORITY_MAP[priority.value] == expected


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------

def test_create_issue_returns_key():
    jira = _jira()
    with patch("httpx.post", return_value=_mock_post("DEV-42")) as mock_post:
        key = jira.create_issue(_ticket())
    assert key == "DEV-42"
    assert mock_post.called


def test_create_issue_payload_structure():
    jira = _jira("PROJ")
    captured: list[dict] = []

    def _capture(*args, **kwargs):
        captured.append(kwargs.get("json", {}))
        return _mock_post("PROJ-1")

    with patch("httpx.post", side_effect=_capture):
        jira.create_issue(_ticket(title="My title", priority=Priority.CRITICAL))

    payload = captured[0]["fields"]
    assert payload["project"]["key"] == "PROJ"
    assert payload["summary"] == "My title"
    assert payload["priority"]["name"] == "Highest"
    assert payload["issuetype"]["name"] == "Story"


def test_create_issue_stamps_antcrew_label():
    """create_issue must include antcrew:<ticket_id> label for upsert lookups."""
    jira = _jira()
    captured: list[dict] = []

    def _capture(*args, **kwargs):
        captured.append(kwargs.get("json", {}))
        return _mock_post("DEV-1")

    with patch("httpx.post", side_effect=_capture):
        jira.create_issue(_ticket(id_="TICKET-AABBCCDD"))

    labels = captured[0]["fields"]["labels"]
    assert "antcrew:TICKET-AABBCCDD" in labels


def test_create_issue_correct_url():
    jira = JiraIntegration(
        url="https://myorg.atlassian.net",
        email="u@u.com",
        api_token="tok",
        project_key="X",
    )
    with patch("httpx.post", return_value=_mock_post()) as mock_post:
        jira.create_issue(_ticket())
    url = mock_post.call_args[0][0]
    assert url == "https://myorg.atlassian.net/rest/api/3/issue"


def test_create_issue_raises_on_http_error():
    jira = _jira()
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = Exception("403 Forbidden")
    with patch("httpx.post", return_value=err_resp):
        with pytest.raises(Exception, match="403"):
            jira.create_issue(_ticket())


# ---------------------------------------------------------------------------
# sync_tickets — idempotency (core new behaviour)
# ---------------------------------------------------------------------------

def test_sync_tickets_creates_when_not_found():
    """First run: label lookup returns empty, so a new issue is created."""
    jira = _jira()
    ticket = _ticket("TICKET-03CE2BBF")

    with patch("httpx.get", return_value=_mock_get_empty()), \
         patch("httpx.post", return_value=_mock_post("DEV-10")) as mock_post:
        pairs = jira.sync_tickets([ticket])

    assert pairs == [(ticket, "DEV-10")]
    mock_post.assert_called_once()


def test_sync_tickets_updates_when_found():
    """Re-run: label lookup finds existing issue → PUT update, no POST create."""
    jira = _jira()
    ticket = _ticket("TICKET-03CE2BBF")

    put_resp = MagicMock()
    put_resp.raise_for_status.return_value = None

    with patch("httpx.get", return_value=_mock_get_found("DEV-5")), \
         patch("httpx.put", return_value=put_resp) as mock_put, \
         patch("httpx.post") as mock_post:
        pairs = jira.sync_tickets([ticket])

    assert pairs == [(ticket, "DEV-5")]
    mock_put.assert_called_once()
    mock_post.assert_not_called()


def test_sync_tickets_returns_pairs():
    jira = _jira()
    tickets = [_ticket("TICKET-AAAA0001"), _ticket("TICKET-BBBB0002")]
    post_keys = iter(["DEV-1", "DEV-2"])

    def _post_side(*args, **kwargs):
        r = MagicMock()
        r.json.return_value = {"key": next(post_keys)}
        r.raise_for_status.return_value = None
        return r

    with patch("httpx.get", return_value=_mock_get_empty()), \
         patch("httpx.post", side_effect=_post_side):
        pairs = jira.sync_tickets(tickets)

    assert len(pairs) == 2
    assert pairs[0] == (tickets[0], "DEV-1")
    assert pairs[1] == (tickets[1], "DEV-2")


def test_sync_tickets_empty_list():
    jira = _jira()
    with patch("httpx.post") as mock_post, patch("httpx.get") as mock_get:
        pairs = jira.sync_tickets([])
    assert pairs == []
    mock_post.assert_not_called()
    mock_get.assert_not_called()


def test_sync_tickets_order_preserved():
    jira = _jira()
    n = 5
    tickets = [_ticket(f"TICKET-{i:08X}") for i in range(n)]
    counter = [0]

    def _post_side(*_a, **_kw):
        r = MagicMock()
        r.json.return_value = {"key": f"DEV-{counter[0]}"}
        r.raise_for_status.return_value = None
        counter[0] += 1
        return r

    with patch("httpx.get", return_value=_mock_get_empty()), \
         patch("httpx.post", side_effect=_post_side):
        pairs = jira.sync_tickets(tickets)

    assert [t.id for t, _ in pairs] == [t.id for t in tickets]


def test_sync_tickets_mixed_create_and_update():
    """Some tickets exist, others are new — both cases handled in one call."""
    jira = _jira()
    t_new = _ticket("TICKET-NEWWWWWW")
    t_existing = _ticket("TICKET-EXISTTT")

    get_responses = iter([_mock_get_empty(), _mock_get_found("DEV-99")])
    put_resp = MagicMock()
    put_resp.raise_for_status.return_value = None

    with patch("httpx.get", side_effect=get_responses), \
         patch("httpx.post", return_value=_mock_post("DEV-100")) as mock_post, \
         patch("httpx.put", return_value=put_resp) as mock_put:
        pairs = jira.sync_tickets([t_new, t_existing])

    assert pairs[0][1] == "DEV-100"   # created
    assert pairs[1][1] == "DEV-99"    # updated
    mock_post.assert_called_once()
    mock_put.assert_called_once()


def test_sync_tickets_lookup_failure_falls_back_to_create():
    """If the label search raises, fall back to creating a new issue."""
    jira = _jira()
    ticket = _ticket("TICKET-FAILSRCH")

    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = Exception("500 search failed")

    with patch("httpx.get", return_value=err_resp), \
         patch("httpx.post", return_value=_mock_post("DEV-7")) as mock_post:
        pairs = jira.sync_tickets([ticket])

    assert pairs == [(ticket, "DEV-7")]
    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# get_project_issues
# ---------------------------------------------------------------------------

def test_get_project_issues():
    jira = _jira("DEV")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "issues": [{"key": "DEV-1", "fields": {"summary": "First issue"}}]
    }
    fake_resp.raise_for_status.return_value = None
    with patch("httpx.get", return_value=fake_resp):
        issues = jira.get_project_issues()
    assert len(issues) == 1
    assert issues[0]["key"] == "DEV-1"


def test_get_project_issues_empty():
    jira = _jira()
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"issues": []}
    fake_resp.raise_for_status.return_value = None
    with patch("httpx.get", return_value=fake_resp):
        assert jira.get_project_issues() == []
