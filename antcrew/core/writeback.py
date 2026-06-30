"""Write generated artifacts back to their real paths on disk.

write_back() resolves each artifact's file_path relative to project_root,
optionally shows a unified diff for files that already exist, and writes
or skips based on the dry_run / yes flags.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WriteEntry:
    file_path: str
    operation: str        # "create" | "modify"
    artifact_type: str    # "code" | "test" | "devops" | "doc"
    written: bool = False
    skipped: bool = False


@dataclass
class WriteBackResult:
    project_root: Path
    entries: list[WriteEntry] = field(default_factory=list)

    @property
    def created(self) -> list[WriteEntry]:
        return [e for e in self.entries if e.operation == "create" and e.written]

    @property
    def modified(self) -> list[WriteEntry]:
        return [e for e in self.entries if e.operation == "modify" and e.written]

    @property
    def skipped(self) -> list[WriteEntry]:
        return [e for e in self.entries if e.skipped]

    @property
    def total_written(self) -> int:
        return sum(1 for e in self.entries if e.written)


def _collect_artifacts(raw: dict) -> list[tuple[str, str, str]]:
    """Return [(file_path, content, artifact_type)] from a raw state dict."""
    results: list[tuple[str, str, str]] = []

    for a in raw.get("code_artifacts") or []:
        fp = getattr(a, "file_path", None) or (a.get("file_path") if isinstance(a, dict) else None)
        ct = getattr(a, "content", None) or (a.get("content") if isinstance(a, dict) else None)
        if fp and ct is not None:
            results.append((fp, ct, "code"))

    for a in raw.get("test_artifacts") or []:
        fp = getattr(a, "file_path", None) or (a.get("file_path") if isinstance(a, dict) else None)
        ct = getattr(a, "content", None) or (a.get("content") if isinstance(a, dict) else None)
        if fp and ct is not None:
            results.append((fp, ct, "test"))

    for a in raw.get("devops_artifacts") or []:
        fp = getattr(a, "file_path", None) or (a.get("file_path") if isinstance(a, dict) else None)
        ct = getattr(a, "content", None) or (a.get("content") if isinstance(a, dict) else None)
        if fp and ct is not None:
            results.append((fp, ct, "devops"))

    for a in raw.get("documentation_artifacts") or []:
        fp = getattr(a, "file_path", None) or (a.get("file_path") if isinstance(a, dict) else None)
        ct = getattr(a, "content", None) or (a.get("content") if isinstance(a, dict) else None)
        if fp and ct is not None:
            results.append((fp, ct, "doc"))

    return results


def _unified_diff(old: str, new: str, filename: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    ))


def _smart_apply(old: str, new: str) -> str:
    """Apply only the changed hunks from *new* onto *old*.

    Equal regions are kept from *old* exactly (preserving whitespace, line endings,
    any incidental differences the agent didn't intentionally change).
    Changed regions are taken from *new*.

    This is preferable to full-file replacement when the agent regenerated a whole
    file but only a handful of lines were intentionally different — it avoids
    overwriting unchanged code with slightly reformatted or miscopied variants.

    Returns the merged string.  If *old* and *new* are identical the original
    content is returned unchanged.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    result: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result.extend(old_lines[i1:i2])
        else:
            result.extend(new_lines[j1:j2])
    return "".join(result)


def write_back(
    state,
    project_root: Path,
    *,
    dry_run: bool = False,
    yes: bool = False,
    patch_mode: bool = False,
    confirm_fn=None,
    print_fn=None,
) -> WriteBackResult:
    """Write artifacts from *state* to *project_root*.

    Args:
        state:        RunResult, dict, or object with .state attribute.
        project_root: Base directory — artifact file_paths are resolved relative to it.
        dry_run:      If True, report what would be written but don't write anything.
        yes:          If True, write modified files without confirmation.
        patch_mode:   If True, apply only changed hunks to existing files instead of
                      replacing them entirely.  Equal lines are kept from the original
                      (preserving exact formatting for unmodified code).
        confirm_fn:   Optional callable(prompt) -> bool for interactive confirmation.
        print_fn:     Optional callable(msg) for status messages.
    """
    if print_fn is None:
        print_fn = print

    raw: dict = (
        state.state
        if hasattr(state, "state")
        else (state if isinstance(state, dict) else {})
    )

    artifacts = _collect_artifacts(raw)
    result = WriteBackResult(project_root=project_root)

    if not artifacts:
        print_fn("[dim]No file artifacts found in state.[/dim]")
        return result

    root_resolved = project_root.resolve()

    for file_path, content, atype in artifacts:
        # Resolve: strip leading slash so Path doesn't treat it as absolute
        rel = file_path.lstrip("/\\")
        target = (project_root / rel).resolve()

        # Guard against path traversal (e.g. file_path = "../../etc/passwd")
        if not target.is_relative_to(root_resolved):
            print_fn(
                f"  [red]SECURITY: skipped[/] {file_path!r} "
                f"— resolves outside project root ({target})"
            )
            entry = WriteEntry(
                file_path=file_path, operation="create",
                artifact_type=atype, skipped=True,
            )
            result.entries.append(entry)
            continue

        exists = target.exists()
        operation = "modify" if exists else "create"

        entry = WriteEntry(
            file_path=file_path,
            operation=operation,
            artifact_type=atype,
        )
        result.entries.append(entry)

        if dry_run:
            op_str = "[yellow]modify[/]" if operation == "modify" else "[green]create[/]"
            print_fn(f"  {op_str}  {rel}")
            continue

        if operation == "modify":
            old_content = target.read_text(encoding="utf-8", errors="replace")
            # In patch-mode, compute the final content by merging only changed hunks.
            final_content = _smart_apply(old_content, content) if patch_mode else content

            diff = _unified_diff(old_content, final_content, rel)
            if not diff:
                print_fn(f"  [dim]no change[/dim]  {rel}")
                entry.skipped = True
                continue

            if not yes:
                print_fn(f"\n[bold]{rel}[/bold] [yellow](modify{' — patch mode' if patch_mode else ''})[/yellow]")
                print_fn(diff)
                if confirm_fn is not None:
                    if not confirm_fn(f"Write {rel}? [y/N] "):
                        entry.skipped = True
                        print_fn(f"  [dim]skipped[/dim]  {rel}")
                        continue

            content = final_content  # write the (possibly patch-merged) content

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")  # target already resolved/validated
        entry.written = True
        op_str = "[yellow]modified[/]" if operation == "modify" else "[green]created[/]"
        print_fn(f"  {op_str}  {rel}")

    return result
