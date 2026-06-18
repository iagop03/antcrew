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

    def create_pr(
        self,
        state: "TeamState",
        *,
        branch_prefix: str = "antcrew/",
        artifacts_key: str = "code_artifacts",
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
        return pr["html_url"]

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
