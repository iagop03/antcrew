"""
Confluence integration — publish pipeline artifacts as Confluence pages.

Requires: httpx (core dependency, already installed).

Usage:
    from antcrew.integrations.confluence import ConfluenceIntegration

    cf = ConfluenceIntegration(
        url="https://yourorg.atlassian.net",
        email="you@yourorg.com",
        api_token=os.environ["CONFLUENCE_API_TOKEN"],
    )
    state = team.run("Build auth module")

    cf.publish_prd(state, space_key="DEV")
    cf.publish_research(state, space_key="RESEARCH")
    cf.publish_docs(state, space_key="DEV", parent_title="AntCrew — Auth Module")
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from antcrew.core.state import TeamState

_API_V1 = "/wiki/rest/api"


# ---------------------------------------------------------------------------
# Markdown → Confluence Storage Format (XHTML subset)
# ---------------------------------------------------------------------------

def _md_to_storage(md: str) -> str:
    """
    Convert a subset of Markdown to Confluence Storage Format (XHTML).

    Handles: headers, bold, italic, code blocks, inline code, bullet lists,
    numbered lists, horizontal rules, and plain paragraphs.
    """
    lines = md.split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_ul = False
    in_ol = False

    def _flush_list():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def _inline(text: str) -> str:
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
        return text

    for line in lines:
        # Fenced code blocks
        if line.startswith("```"):
            if not in_code:
                _flush_list()
                in_code = True
                code_lang = line[3:].strip() or "none"
                code_lines = []
            else:
                in_code = False
                code_body = "\n".join(code_lines)
                out.append(
                    f'<ac:structured-macro ac:name="code">'
                    f'<ac:parameter ac:name="language">{code_lang}</ac:parameter>'
                    f"<ac:plain-text-body><![CDATA[{code_body}]]></ac:plain-text-body>"
                    f"</ac:structured-macro>"
                )
            continue

        if in_code:
            code_lines.append(line)
            continue

        # Headers
        header = re.match(r"^(#{1,6})\s+(.*)", line)
        if header:
            _flush_list()
            level = len(header.group(1))
            out.append(f"<h{level}>{_inline(header.group(2))}</h{level}>")
            continue

        # Horizontal rule
        if re.match(r"^---+$", line.strip()):
            _flush_list()
            out.append("<hr/>")
            continue

        # Unordered list
        ul_match = re.match(r"^[-*]\s+(.*)", line)
        if ul_match:
            if not in_ul:
                if in_ol:
                    out.append("</ol>")
                    in_ol = False
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(ul_match.group(1))}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^\d+\.\s+(.*)", line)
        if ol_match:
            if not in_ol:
                if in_ul:
                    out.append("</ul>")
                    in_ul = False
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(ol_match.group(1))}</li>")
            continue

        # End any open list
        if line.strip() == "":
            _flush_list()
            out.append("")
            continue

        _flush_list()
        out.append(f"<p>{_inline(line)}</p>")

    _flush_list()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ConfluenceIntegration
# ---------------------------------------------------------------------------

class ConfluenceIntegration:
    """
    Publishes pipeline artifacts (PRD, research documents, generated docs)
    as Confluence pages via the Confluence REST API v1.

    Authentication: Basic Auth using an Atlassian API token.
    """

    def __init__(self, url: str, email: str, api_token: str) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise ImportError("httpx is required. pip install httpx") from exc

        self._base = url.rstrip("/")
        self._auth = (email, api_token)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: dict) -> dict:
        import httpx
        r = httpx.post(
            f"{self._base}{_API_V1}/{endpoint}",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _put(self, endpoint: str, payload: dict) -> dict:
        import httpx
        r = httpx.put(
            f"{self._base}{_API_V1}/{endpoint}",
            json=payload,
            auth=self._auth,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def _get(self, endpoint: str) -> dict:
        import httpx
        r = httpx.get(
            f"{self._base}{_API_V1}/{endpoint}",
            auth=self._auth,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Page management
    # ------------------------------------------------------------------

    def _find_page(self, space_key: str, title: str) -> Optional[dict]:
        """Return the existing page dict if it exists, else None."""
        import urllib.parse
        encoded = urllib.parse.quote(title)
        data = self._get(f"content?spaceKey={space_key}&title={encoded}&expand=version")
        results = data.get("results", [])
        return results[0] if results else None

    def create_or_update_page(
        self,
        space_key: str,
        title: str,
        body_markdown: str,
        *,
        parent_title: Optional[str] = None,
    ) -> dict:
        """
        Create a new Confluence page or update it if it already exists.

        Returns the raw Confluence page dict.
        """
        storage_body = _md_to_storage(body_markdown)

        ancestors: list[dict] = []
        if parent_title:
            parent = self._find_page(space_key, parent_title)
            if parent:
                ancestors = [{"id": parent["id"]}]

        existing = self._find_page(space_key, title)

        if existing:
            version = existing["version"]["number"] + 1
            return self._put(f"content/{existing['id']}", {
                "version": {"number": version},
                "title": title,
                "type": "page",
                "body": {"storage": {"value": storage_body, "representation": "storage"}},
            })

        payload: dict = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": storage_body, "representation": "storage"}},
        }
        if ancestors:
            payload["ancestors"] = ancestors
        return self._post("content", payload)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def publish_prd(
        self,
        state: "TeamState",
        space_key: str,
        *,
        parent_title: Optional[str] = None,
    ) -> Optional[dict]:
        """Publish the PRD as a Confluence page. Returns the page dict."""
        prd = state.get("prd")
        if not prd:
            return None

        lines = [
            f"# {prd.title}\n",
            f"{prd.summary}\n",
            "## Goals\n",
            *[f"- {g}" for g in prd.goals],
            "\n## Functional Requirements\n",
            *[f"- {r}" for r in prd.functional_requirements],
        ]
        if prd.non_functional_requirements:
            lines += ["\n## Non-Functional Requirements\n",
                      *[f"- {r}" for r in prd.non_functional_requirements]]
        if prd.out_of_scope:
            lines += ["\n## Out of Scope\n", *[f"- {o}" for o in prd.out_of_scope]]
        if prd.open_questions:
            lines += ["\n## Open Questions\n", *[f"- {q}" for q in prd.open_questions]]

        return self.create_or_update_page(
            space_key,
            f"PRD — {prd.title}",
            "\n".join(lines),
            parent_title=parent_title,
        )

    def publish_research(
        self,
        state: "TeamState",
        space_key: str,
        *,
        parent_title: Optional[str] = None,
    ) -> Optional[dict]:
        """Publish the ResearchDocument as a Confluence page."""
        doc = state.get("research_document")
        if not doc:
            return None

        lines = [f"# {doc.title}\n", f"**Topic:** {doc.topic}\n",
                 "## Key Findings\n", *[f"- {f}" for f in doc.key_findings], ""]
        for section in doc.sections:
            lines += [f"## {section.heading}\n", section.content, ""]
        if doc.sources:
            lines += ["## Sources\n", *[f"- {s}" for s in doc.sources]]

        return self.create_or_update_page(
            space_key,
            doc.title,
            "\n".join(lines),
            parent_title=parent_title,
        )

    def publish_docs(
        self,
        state: "TeamState",
        space_key: str,
        *,
        parent_title: Optional[str] = None,
    ) -> list[dict]:
        """
        Publish every DocumentationArtifact as a separate Confluence page.

        Returns a list of page dicts (one per artifact).
        """
        doc_artifacts = state.get("doc_artifacts") or []
        pages: list[dict] = []
        for artifact in doc_artifacts:
            page = self.create_or_update_page(
                space_key,
                artifact.title,
                artifact.content,
                parent_title=parent_title,
            )
            pages.append(page)
        return pages
