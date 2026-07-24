"""
GitHub integration — creates a branch, commits code artifacts, and opens a PR.

Requires: httpx (core dependency, already installed).

Usage:
    from antcrew.integrations.github import GitHubIntegration

    gh = GitHubIntegration(
        token=os.environ["GITHUB_TOKEN"],
        repo="your-org/your-repo",        # "owner/repo"
        base_branch="main",               # optional, default "main"
    )
    state = team.run("Build auth module")
    pr_url = gh.create_pr(state)
    print("PR:", pr_url)

    # DevOps artifacts only:
    pr_url = gh.create_pr(state, artifacts_key="devops_artifacts")
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.core.state import TeamState

_API = "https://api.github.com"


class GitHubIntegration:
    """
    Creates GitHub PRs with code and/or DevOps artifacts from the pipeline state.

    Authentication: a personal access token (classic) or fine-grained token
    with ``Contents: write`` and ``Pull requests: write`` permissions.
    """

    def __init__(
        self,
        token: str,
        repo: str,
        base_branch: str = "main",
    ) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise ImportError("httpx is required. pip install httpx") from exc

        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._repo = repo
        self._base_branch = base_branch

    # ------------------------------------------------------------------
    # Low-level HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict:
        import httpx
        r = httpx.get(f"{_API}{path}", headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        import httpx
        r = httpx.post(f"{_API}{path}", json=payload, headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, payload: dict) -> dict:
        import httpx
        r = httpx.put(f"{_API}{path}", json=payload, headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_sha(self) -> str:
        data = self._get(f"/repos/{self._repo}/git/ref/heads/{self._base_branch}")
        return data["object"]["sha"]

    def _create_branch(self, branch: str, sha: str) -> None:
        self._post(f"/repos/{self._repo}/git/refs", {
            "ref": f"refs/heads/{branch}",
            "sha": sha,
        })

    def _upsert_file(self, branch: str, path: str, content: str, message: str) -> None:
        """Create or update a file on ``branch``."""
        import httpx
        encoded = base64.b64encode(content.encode()).decode()
        payload: dict = {"message": message, "content": encoded, "branch": branch}

        # GET to find the blob SHA if the file already exists
        r = httpx.get(
            f"{_API}/repos/{self._repo}/contents/{path}",
            headers={**self._headers, "ref": branch},
            timeout=30,
        )
        if r.status_code == 200:
            payload["sha"] = r.json()["sha"]

        self._put(f"/repos/{self._repo}/contents/{path}", payload)

    @staticmethod
    def _slug(title: str) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", title.lower())[:40].strip("-")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post_comment(self, pr_url: str, body: str) -> None:
        """Post a comment on an existing GitHub PR.

        Parameters
        ----------
        pr_url:
            The full HTML URL of the PR, e.g. ``https://github.com/org/repo/pull/42``.
        body:
            Markdown-formatted comment body.
        """
        import re as _re
        m = _re.search(r"github\.com/(.+)/pull/(\d+)", pr_url)
        if not m:
            raise ValueError(f"Cannot parse PR URL: {pr_url!r}")
        repo, pr_number = m.group(1), m.group(2)
        self._post(f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})

    def create_pr(
        self,
        state: "TeamState",
        *,
        branch_prefix: str = "antcrew/",
        artifacts_key: str = "code_artifacts",
        post_summary: bool = True,
    ) -> str:
        """
        Commit all artifacts from ``state[artifacts_key]`` to a new branch and open a PR.

        Parameters
        ----------
        state:
            The final LangGraph state returned by ``team.run()``.
        branch_prefix:
            Prefix for the auto-generated branch name.
        artifacts_key:
            Which state key to pull artifacts from.
            Typical values: ``"code_artifacts"`` or ``"devops_artifacts"``.
        post_summary:
            If ``True`` (default), automatically posts an explainability summary
            comment on the newly opened PR listing what was built and why.

        Returns
        -------
        str
            The URL of the newly created GitHub PR.
        """
        prd = state.get("prd")
        artifacts = state.get(artifacts_key) or []

        if not artifacts:
            raise ValueError(
                f"state['{artifacts_key}'] is empty — nothing to commit.\n"
                "Make sure the pipeline ran at least the dev agents."
            )

        title = prd.title if prd else "AntCrew generated code"
        branch = f"{branch_prefix}{self._slug(title)}-{datetime.now().strftime('%Y%m%d-%H%M')}"

        sha = self._base_sha()
        self._create_branch(branch, sha)

        for artifact in artifacts:
            ticket_ref = getattr(artifact, "ticket_id", "infra")
            self._upsert_file(
                branch,
                artifact.file_path,
                artifact.content,
                f"feat({ticket_ref}): add {artifact.file_path}",
            )

        # Optionally commit devops artifacts alongside code artifacts
        if artifacts_key == "code_artifacts":
            for da in (state.get("devops_artifacts") or []):
                self._upsert_file(
                    branch,
                    da.file_path,
                    da.content,
                    f"chore(infra): add {da.file_path}",
                )

        pr = self._post(f"/repos/{self._repo}/pulls", {
            "title": title,
            "body":  self._build_body(state),
            "head":  branch,
            "base":  self._base_branch,
        })
        pr_url: str = pr["html_url"]

        if post_summary:
            try:
                self.post_comment(pr_url, self._build_summary_comment(state))
            except Exception:
                pass  # never block the PR URL return on a comment failure

        return pr_url

    def _build_summary_comment(self, state: "TeamState") -> str:
        """Build a markdown explainability report posted as a PR comment."""
        prd      = state.get("prd")
        tickets  = state.get("tickets") or []
        code     = state.get("code_artifacts") or []
        devops   = state.get("devops_artifacts") or []
        reviews  = state.get("review_feedback") or []

        lines: list[str] = ["## AntCrew — Explainability Report\n"]

        if prd:
            lines += [f"**Objective:** {prd.summary}\n"]

        if tickets:
            lines.append("### Tickets addressed\n")
            for t in tickets:
                lines.append(f"- `{t.id}` **{t.title}** _{t.priority.value}_")
            lines.append("")

        total_files = len(code) + len(devops)
        if total_files:
            lines.append(f"### Files generated ({total_files})\n")
            for a in code:
                lines.append(f"- `{a.file_path}` — {a.description}")
            for a in devops:
                lines.append(f"- `{a.file_path}` — {a.description} _(infra)_")
            lines.append("")

        if reviews:
            lines.append("### Review feedback applied\n")
            for r in reviews:
                comment = getattr(r, "comment", None) or str(r)
                lines.append(f"- {comment}")
            lines.append("")

        lines.append("---\n_Generated by [AntCrew](https://antcrew-int.fly.dev)_")
        return "\n".join(lines)

    def create_engine_pr(
        self,
        goal: str,
        artifacts: list,
        *,
        conditions: "dict[str, str] | None" = None,
        capabilities: "list[dict] | None" = None,
        branch_prefix: str = "antcrew-engine/",
        post_summary: bool = True,
    ) -> str:
        """Commit engine artifacts to a new branch and open a PR with a TraceLog comment.

        Parameters
        ----------
        goal:
            The natural-language goal the engine was given.
        artifacts:
            Artifact objects (with ``file_path`` + ``content`` attrs) **or** plain
            ``{"file_path": str, "content": str}`` dicts — e.g. from
            ``Run.state["code_artifacts"]`` on the platform.
        conditions:
            Mapping of condition_id → ``"satisfied" | "pending" | "not_reached"``.
            Shown as a satisfaction checklist in the PR comment.
        capabilities:
            List of executed capabilities, each with keys ``name``, ``duration_s``,
            ``cost_usd``, ``produced``. Shown as the capability trace in the PR comment.
        branch_prefix:
            Prefix for the auto-generated branch name.
        post_summary:
            If ``True`` (default), posts an engine-specific explainability comment.

        Returns
        -------
        str
            The URL of the newly created GitHub PR.
        """
        if not artifacts:
            raise ValueError("artifacts is empty — nothing to commit")

        slug = self._slug(goal[:60])
        branch = f"{branch_prefix}{slug}-{datetime.now().strftime('%Y%m%d-%H%M')}"

        sha = self._base_sha()
        self._create_branch(branch, sha)

        for art in artifacts:
            if isinstance(art, dict):
                file_path = art.get("file_path") or art.get("path") or ""
                content   = art.get("content") or ""
            else:
                file_path = getattr(art, "file_path", None) or str(getattr(art, "id", ""))
                content   = getattr(art, "content", "") or ""
            if file_path:
                self._upsert_file(branch, file_path, content, f"feat: add {file_path}")

        pr = self._post(f"/repos/{self._repo}/pulls", {
            "title": f"AntCrew Engine: {goal[:72]}",
            "body":  self._build_engine_body(goal, artifacts, conditions, capabilities),
            "head":  branch,
            "base":  self._base_branch,
        })
        pr_url: str = pr["html_url"]

        if post_summary:
            try:
                self.post_comment(
                    pr_url,
                    self._build_engine_summary_comment(goal, conditions, capabilities, len(artifacts)),
                )
            except Exception:
                pass

        return pr_url

    def _build_engine_summary_comment(
        self,
        goal: str,
        conditions: "dict[str, str] | None",
        capabilities: "list[dict] | None",
        artifact_count: int,
    ) -> str:
        """Engine-specific explainability comment with TraceLog (conditions + capabilities)."""
        lines: list[str] = ["## AntCrew Engine — Explainability Report\n"]
        lines.append(f"**Goal:** {goal}\n")
        lines.append(f"**Artifacts committed:** {artifact_count}\n")

        if conditions:
            lines.append("### Goal conditions\n")
            icons = {"satisfied": "✅", "pending": "⏳", "not_reached": "⬜"}
            for cond_id, status in sorted(conditions.items()):
                icon = icons.get(status, "⬜")
                lines.append(f"- {icon} `{cond_id}` — {status}")
            lines.append("")

        if capabilities:
            total_cost = sum(c.get("cost_usd") or 0.0 for c in capabilities)
            total_dur  = sum(c.get("duration_s") or 0.0 for c in capabilities)
            lines.append(
                f"### Capability trace ({len(capabilities)} steps · "
                f"${total_cost:.4f} · {total_dur:.1f}s)\n"
            )
            for cap in capabilities:
                name     = cap.get("name", "?")
                dur      = cap.get("duration_s") or 0.0
                cost     = cap.get("cost_usd") or 0.0
                produced = cap.get("produced") or []
                produced_str = ", ".join(f"`{p}`" for p in produced) if produced else "—"
                lines.append(
                    f"- **{name}** {dur:.1f}s ${cost:.4f} → {produced_str}"
                )
            lines.append("")

        lines.append("---\n_Generated by [AntCrew Engine](https://antcrew-int.fly.dev)_")
        return "\n".join(lines)

    def _build_engine_body(
        self,
        goal: str,
        artifacts: list,
        conditions: "dict[str, str] | None",
        capabilities: "list[dict] | None",
    ) -> str:
        lines: list[str] = [f"## AntCrew Engine — {goal[:80]}\n"]

        if conditions:
            satisfied = sum(1 for s in conditions.values() if s == "satisfied")
            lines.append(
                f"**Goal conditions:** {satisfied}/{len(conditions)} satisfied\n"
            )

        file_count = len(artifacts)
        if file_count:
            lines.append(f"**Files committed:** {file_count}\n")
            for art in artifacts[:20]:
                path = art.get("file_path") if isinstance(art, dict) else getattr(art, "file_path", "")
                if path:
                    lines.append(f"- `{path}`")
            if file_count > 20:
                lines.append(f"- … and {file_count - 20} more")
            lines.append("")

        lines.append("---\n_Generated by [AntCrew Engine](https://github.com/your-org/antcrew)_")
        return "\n".join(lines)

    def _build_body(self, state: "TeamState") -> str:
        prd      = state.get("prd")
        tickets  = state.get("tickets") or []
        code     = state.get("code_artifacts") or []
        devops   = state.get("devops_artifacts") or []

        lines: list[str] = ["## AntCrew Generated PR\n"]

        if prd:
            lines += [f"### {prd.title}\n", prd.summary, ""]

        if tickets:
            lines.append("### Tickets\n")
            for t in tickets:
                lines.append(f"- **{t.id}** {t.title} `{t.priority.value}`")
            lines.append("")

        if code:
            lines.append("### Code Files\n")
            for a in code:
                lines.append(f"- `{a.file_path}` — {a.description}")
            lines.append("")

        if devops:
            lines.append("### DevOps Files\n")
            for a in devops:
                lines.append(f"- `{a.file_path}` — {a.description}")
            lines.append("")

        lines.append("---\n_Generated by [AntCrew](https://github.com/your-org/antcrew)_")
        return "\n".join(lines)
