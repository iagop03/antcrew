from __future__ import annotations

import json

from antcrew.core.agent import BaseAgent, _json_loads, _strip_fences
from antcrew.core.artifacts import (
    ArtifactContract, ContractError, CodeArtifact, CodeReview, Ticket, TicketStatus, coerce_list,
)
from antcrew.core.state import TeamState

_REVIEW_CONTRACT: ArtifactContract[CodeReview] = ArtifactContract("review", CodeReview)

_PLAN_SYSTEM = """\
You are a Senior Backend Developer.
Given a development ticket, list ALL files you need to create or modify — no content yet.

Respond ONLY with a valid JSON array (no markdown fences, no prose):
[
  {"file_path": "src/...", "description": "What this file does", "language": "python"},
  ...
]

Rules:
- List ONLY files strictly required to fulfil the ticket.
- No file contents — paths and descriptions only.
- Keep the list focused: omit files the ticket does not require.
"""

_FILE_SYSTEM = """\
You are a Senior Backend Developer.
Generate the COMPLETE content for exactly ONE backend file.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{{
  "file_path": "src/...",
  "description": "What this file does",
  "language": "python",
  "content": "...full file content..."
}}

Rules:
- Write clean, idiomatic, production-quality code.
- Complete implementation — no TODOs or placeholders.
- Do NOT produce frontend/UI code.
"""

_REFINE_SYSTEM = """\
You are a Senior Backend Developer. The reviewer provided feedback on your code.
Update the code artifacts to address the feedback while keeping everything else intact.

Current code artifacts:
{artifact_json}

Reviewer feedback:
{feedback}

Respond ONLY with the complete updated JSON array of code artifact objects (no markdown fences, no prose).
Each object must include: ticket_id, file_path, description, language, content.
"""

_FIX_SYSTEM = """\
You are a Senior Backend Developer fixing a specific file after a code review.
A reviewer flagged CRITICAL or ERROR issues in this file. Fix ONLY what is listed.
Do not rewrite unrelated parts — keep the rest of the file intact.

Respond ONLY with a valid JSON object (no markdown fences, no prose):
{
  "file_path": "src/...",
  "description": "What this file does",
  "language": "python",
  "content": "...complete fixed file content..."
}
"""

_TEST_FIX_SYSTEM = """\
You are a Senior Backend Developer. Automated tests ran on your generated code and FAILED.
Fix the code so the tests pass. Analyse the failures carefully before changing anything.

Test output (last 2 000 chars):
{errors}

Current code artifacts (JSON):
{code}

Rules:
- Fix ONLY what is needed to make the tests pass.
- Keep all existing functionality intact.
- Respond ONLY with a valid JSON array of ALL code artifact objects (no markdown fences),
  including both fixed and unchanged files.
  Each object: ticket_id, file_path, description, language, content.
"""


def _parse_list(raw: str) -> list[dict]:
    stripped = _strip_fences(raw)
    if not stripped:
        return []
    try:
        return _json_loads(stripped)
    except Exception:
        return []


def _parse_obj(raw: str) -> dict:
    stripped = _strip_fences(raw)
    if not stripped:
        return {}
    try:
        result = _json_loads(stripped)
        if isinstance(result, dict):
            return result
        # LLM returned a list instead of an object — take the first element.
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0]
        return {}
    except Exception:
        return {}


class BackendDevAgent(BaseAgent):
    name = "backend_dev"
    role_description = "Implements open tickets as code artifacts."
    conversational = True
    consumes: list[str] = ["tickets", "metadata", "review", "code_artifacts"]
    produces: list[str] = ["code_artifacts", "tickets"]

    def _fix_flagged_files(self, state: TeamState) -> dict | None:
        """Fix-mode: re-generate only files with critical/error reviewer findings."""
        meta = state.get("metadata") or {}
        if not meta.get("reviewer_fix_requested"):
            return None
        try:
            review = _REVIEW_CONTRACT.extract(state)
        except ContractError:
            return None

        bad_paths = {
            f.file_path for f in review.findings
            if f.severity in ("critical", "error")
        }
        if not bad_paths:
            return None

        code_artifacts = list(coerce_list(state, "code_artifacts", CodeArtifact))
        fixed: list[CodeArtifact] = []
        fixed_count = 0

        for art in code_artifacts:
            if art.file_path not in bad_paths:
                fixed.append(art)
                continue

            findings_text = "\n\n".join(
                f"{f.severity.upper()}: {f.message}\nFix: {f.suggestion}"
                for f in review.findings
                if f.file_path == art.file_path and f.severity in ("critical", "error")
            )
            context = (
                f"File to fix:\n{json.dumps(art.model_dump(), indent=2)}\n\n"
                f"Issues to fix:\n{findings_text}\n\n"
                f"Review summary: {review.summary}"
            )
            file_raw = self.system(_FIX_SYSTEM, context)
            file_data = _parse_obj(file_raw)
            if file_data.get("content") and file_data.get("file_path"):
                fixed.append(
                    CodeArtifact(
                        ticket_id=art.ticket_id,
                        **{k: v for k, v in file_data.items() if k in CodeArtifact.model_fields},
                    )
                )
                fixed_count += 1
            else:
                fixed.append(art)  # keep original if fix failed

        attempt = meta.get("fix_attempts", 1)
        new_meta = {k: v for k, v in meta.items() if k != "reviewer_fix_requested"}
        return {
            "code_artifacts": fixed,
            "current_agent": self.name,
            "metadata": new_meta,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[Dev/Fix] Fixed {fixed_count}/{len(bad_paths)} flagged files "
                    f"(attempt {attempt})."
                ),
            }],
        }

    def run(self, state: TeamState) -> dict:
        # Fix-mode: reviewer sent code back with critical findings to address.
        fix_result = self._fix_flagged_files(state)
        if fix_result is not None:
            return fix_result

        tickets = coerce_list(state, "tickets", Ticket)
        open_tickets = [t for t in tickets if t.status == TicketStatus.OPEN]

        if not open_tickets:
            return {
                "current_agent": self.name,
                "messages": [{"role": "assistant", "content": "[Dev] No open tickets."}],
            }

        query = " ".join(t.title for t in open_tickets[:5])
        repo_ctx = self._search_repo(query) + self._recall(query)
        plan_prompt = _PLAN_SYSTEM + repo_ctx
        file_prompt = _FILE_SYSTEM + repo_ctx

        all_artifacts: list[CodeArtifact] = []
        updated_tickets = list(tickets)

        for ticket in open_tickets:
            ticket_json = ticket.model_dump_json(indent=2)

            # Phase 1 — plan: get the list of files to create (no content).
            plan_raw = self.system(plan_prompt, f"Ticket:\n{ticket_json}")
            file_specs = _parse_list(plan_raw)

            sibling_paths = [s.get("file_path", "") for s in file_specs]

            # Phase 2 — generate each file individually.
            for spec in file_specs:
                context = (
                    f"Ticket:\n{ticket_json}\n\n"
                    f"File to generate:\n{json.dumps(spec, indent=2)}\n\n"
                    f"Other files in this ticket (for imports/references): {sibling_paths}"
                )
                file_raw = self.system(file_prompt, context)
                file_data = _parse_obj(file_raw)
                if file_data.get("content") and file_data.get("file_path"):
                    all_artifacts.append(
                        CodeArtifact(
                            ticket_id=ticket.id,
                            **{k: v for k, v in file_data.items() if k in CodeArtifact.model_fields},
                        )
                    )

            idx = next(i for i, t in enumerate(updated_tickets) if t.id == ticket.id)
            updated_tickets[idx] = ticket.model_copy(update={"status": TicketStatus.DONE})

        # Preserve artifacts from other sprints; replace only the ones we just generated.
        existing = coerce_list(state, "code_artifacts", CodeArtifact)
        current_ids = {t.id for t in open_tickets}
        preserved = [a for a in existing if a.ticket_id not in current_ids]
        return {
            "code_artifacts": preserved + all_artifacts,
            "tickets": updated_tickets,
            "current_agent": self.name,
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        f"[Dev] {len(all_artifacts)} files generated "
                        f"across {len(open_tickets)} tickets."
                    ),
                }
            ],
        }

    def fix_test_failures(self, state: TeamState) -> "dict | None":
        """Re-generate code to fix failing tests.

        Called by :func:`~antcrew.core.feedback.run_test_feedback_loop` when
        the sandbox runner reports test failures.  Expects ``_feedback_error``
        in *state* with the truncated test output.

        Returns a patch dict ``{"code_artifacts": [...]}`` or ``None`` when
        there is nothing to fix (no error in state or no artifacts).
        """
        error = state.get("_feedback_error", "")
        if not error:
            return None
        code_artifacts = coerce_list(state, "code_artifacts", CodeArtifact)
        if not code_artifacts:
            return None

        context = _TEST_FIX_SYSTEM.format(
            errors=error,
            code=json.dumps([a.model_dump() for a in code_artifacts], indent=2),
        )
        raw = self.system(context, "Fix the failing tests.")
        raw_list = _parse_list(raw)
        if not raw_list:
            return None
        updated = [
            CodeArtifact(
                ticket_id=d.get("ticket_id", ""),
                **{k: v for k, v in d.items() if k in CodeArtifact.model_fields and k != "ticket_id"},
            )
            for d in raw_list
            if d.get("content") and d.get("file_path")
        ]
        if not updated:
            return None
        log.info("backend_dev fix_test_failures regenerated %d artifacts", len(updated))
        return {"code_artifacts": updated}

    def refine(self, state: TeamState, artifact: list[CodeArtifact], feedback: str) -> dict:
        raw_artifacts: list[dict] = self.system_parsed(
            _REFINE_SYSTEM.format(
                artifact_json=json.dumps([a.model_dump() for a in artifact], indent=2),
                feedback=feedback,
            ),
            "Revise the code based on the feedback.",
            list[dict],
        )
        updated = [
            CodeArtifact(
                **{k: v for k, v in a.items() if k in CodeArtifact.model_fields}
            )
            for a in raw_artifacts
        ]
        return {"code_artifacts": updated}
