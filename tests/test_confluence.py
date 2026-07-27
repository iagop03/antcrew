"""Tests for ConfluenceIntegration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from antcrew.core.artifacts import (
    PRD,
    DocumentationArtifact,
    ResearchDocument,
    ResearchSection,
)
from antcrew.integrations.confluence import ConfluenceIntegration, _md_to_storage

# ---------------------------------------------------------------------------
# _md_to_storage helper
# ---------------------------------------------------------------------------

def test_md_header_h1():
    assert "<h1>Hello</h1>" in _md_to_storage("# Hello")


def test_md_header_h2():
    assert "<h2>Section</h2>" in _md_to_storage("## Section")


def test_md_bold():
    assert "<strong>text</strong>" in _md_to_storage("**text**")


def test_md_italic():
    assert "<em>text</em>" in _md_to_storage("*text*")


def test_md_inline_code():
    assert "<code>fn()</code>" in _md_to_storage("`fn()`")


def test_md_unordered_list():
    html = _md_to_storage("- item one\n- item two")
    assert "<ul>" in html
    assert "<li>item one</li>" in html
    assert "<li>item two</li>" in html


def test_md_ordered_list():
    html = _md_to_storage("1. first\n2. second")
    assert "<ol>" in html
    assert "<li>first</li>" in html


def test_md_fenced_code():
    html = _md_to_storage("```python\nprint('hi')\n```")
    assert 'ac:name="code"' in html
    assert "python" in html
    assert "print('hi')" in html


def test_md_horizontal_rule():
    assert "<hr/>" in _md_to_storage("---")


def test_md_link():
    html = _md_to_storage("[AntCrew](https://github.com)")
    assert '<a href="https://github.com">AntCrew</a>' in html


def test_md_plain_paragraph():
    html = _md_to_storage("Just some text")
    assert "<p>Just some text</p>" in html


# ---------------------------------------------------------------------------
# ConfluenceIntegration helpers
# ---------------------------------------------------------------------------

def _cf() -> ConfluenceIntegration:
    return ConfluenceIntegration(
        url="https://test.atlassian.net",
        email="user@test.com",
        api_token="token",
    )


def _ok_resp(data: dict) -> MagicMock:
    r = MagicMock()
    r.json.return_value = data
    r.raise_for_status.return_value = None
    return r


def _page(id_: str = "123", version: int = 1) -> dict:
    return {"id": id_, "version": {"number": version}, "title": "Page"}


# ---------------------------------------------------------------------------
# create_or_update_page
# ---------------------------------------------------------------------------

def test_create_page_new():
    cf = _cf()
    get_resp = _ok_resp({"results": []})  # page doesn't exist
    post_resp = _ok_resp({"id": "99"})

    with (
        patch("httpx.get", return_value=get_resp),
        patch("httpx.post", return_value=post_resp) as mock_post,
    ):
        result = cf.create_or_update_page("DEV", "My Page", "# Hello")

    assert result == {"id": "99"}
    assert mock_post.called


def test_create_page_update_existing():
    cf = _cf()
    get_resp = _ok_resp({"results": [_page("42", version=3)]})
    put_resp = _ok_resp({"id": "42"})

    with (
        patch("httpx.get", return_value=get_resp),
        patch("httpx.put", return_value=put_resp) as mock_put,
    ):
        cf.create_or_update_page("DEV", "My Page", "# Hello")

    assert mock_put.called
    put_payload = mock_put.call_args[1]["json"]
    assert put_payload["version"]["number"] == 4  # incremented


def test_create_page_with_parent():
    cf = _cf()
    get_responses = [
        _ok_resp({"results": [_page("parent-id")]}),  # parent lookup
        _ok_resp({"results": []}),                    # page doesn't exist
    ]
    get_iter = iter(get_responses)
    post_resp = _ok_resp({"id": "new"})

    with (
        patch("httpx.get", side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", return_value=post_resp) as mock_post,
    ):
        cf.create_or_update_page("DEV", "Child Page", "content", parent_title="Parent Page")

    payload = mock_post.call_args[1]["json"]
    assert payload.get("ancestors") == [{"id": "parent-id"}]


# ---------------------------------------------------------------------------
# publish_prd
# ---------------------------------------------------------------------------

def test_publish_prd_creates_page():
    cf = _cf()
    state = {
        "prd": PRD(
            title="Auth", summary="JWT auth",
            goals=["Secure login"], out_of_scope=[],
            functional_requirements=["POST /auth"],
            non_functional_requirements=[], open_questions=[],
        ),
        "tickets": None, "code_artifacts": None, "devops_artifacts": None,
        "doc_artifacts": None, "research_document": None, "content_piece": None,
        "messages": [], "request": "", "current_agent": "", "errors": [], "metadata": {},
        "test_artifacts": None, "review": None,
    }
    get_resp = _ok_resp({"results": []})
    post_resp = _ok_resp({"id": "55"})

    with (
        patch("httpx.get", return_value=get_resp),
        patch("httpx.post", return_value=post_resp),
    ):
        result = cf.publish_prd(state, "DEV")

    assert result == {"id": "55"}


def test_publish_prd_no_prd_returns_none():
    cf = _cf()
    state = {"prd": None}
    assert cf.publish_prd(state, "DEV") is None


# ---------------------------------------------------------------------------
# publish_research
# ---------------------------------------------------------------------------

def test_publish_research_creates_page():
    cf = _cf()
    state = {
        "research_document": ResearchDocument(
            title="LLMs at Scale", topic="Deployment challenges",
            key_findings=["Finding 1"],
            sections=[ResearchSection(heading="Intro", content="...")],
            sources=["Source A"],
        ),
        "prd": None, "tickets": None, "code_artifacts": None,
    }
    get_resp = _ok_resp({"results": []})
    post_resp = _ok_resp({"id": "77"})

    with (
        patch("httpx.get", return_value=get_resp),
        patch("httpx.post", return_value=post_resp),
    ):
        result = cf.publish_research(state, "RESEARCH")

    assert result == {"id": "77"}


def test_publish_research_no_document_returns_none():
    cf = _cf()
    assert cf.publish_research({"research_document": None}, "R") is None


# ---------------------------------------------------------------------------
# publish_docs
# ---------------------------------------------------------------------------

def test_publish_docs_creates_one_page_per_artifact():
    cf = _cf()
    state = {
        "doc_artifacts": [
            DocumentationArtifact(file_path="README.md", title="README",
                                  doc_type="readme", format="markdown", content="# Hi"),
            DocumentationArtifact(file_path="docs/ARCH.md", title="Architecture",
                                  doc_type="architecture", format="markdown", content="# Arch"),
        ],
    }
    get_resp = _ok_resp({"results": []})
    post_resp = _ok_resp({"id": "x"})
    call_count = [0]

    def _post_side(*a, **kw):
        call_count[0] += 1
        return post_resp

    with (
        patch("httpx.get", return_value=get_resp),
        patch("httpx.post", side_effect=_post_side),
    ):
        pages = cf.publish_docs(state, "DEV")

    assert len(pages) == 2
    assert call_count[0] == 2


def test_publish_docs_empty_returns_empty():
    cf = _cf()
    assert cf.publish_docs({"doc_artifacts": []}, "DEV") == []
    assert cf.publish_docs({"doc_artifacts": None}, "DEV") == []
