"""Write generated artifacts back to their real paths on disk.

write_back() resolves each artifact's file_path relative to project_root,
optionally shows a unified diff for files that already exist, and writes
or skips based on the dry_run / yes flags.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


def write_back(
    state,
    project_root: Path,
    *,
    dry_run: bool = False,
    yes: bool = False,
    confirm_fn=None,
    print_fn=None,
) -> WriteBackResult:
    """Write artifacts from *state* to *project_root*.

    Args:
        state: RunResult, dict, or object with .state attribute.
        project_root: Base directory — artifact file_paths are resolved relative to it.
        dry_run: If True, report what would be written but don't write anything.
        yes: If True, write modified files without confirmation.
        confirm_fn: Optional callable(prompt) -> bool for interactive confirmation.
        print_fn: Optional callable(msg) for status messages.
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

    for file_path, content, atype in artifacts:
        # Resolve: strip leading slash so Path doesn't treat it as absolute
        rel = file_path.lstrip("/\\")
        target = project_root / rel
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

        if operation == "modify" and not yes:
            old_content = target.read_text(encoding="utf-8", errors="replace")
            diff = _unified_diff(old_content, content, rel)
            if diff:
                print_fn(f"\n[bold]{rel}[/bold] [yellow](modify)[/yellow]")
                print_fn(diff)
            else:
                print_fn(f"  [dim]no change[/dim]  {rel}")
                entry.skipped = True
                continue

            if confirm_fn is not None:
                if not confirm_fn(f"Write {rel}? [y/N] "):
                    entry.skipped = True
                    print_fn(f"  [dim]skipped[/dim]  {rel}")
                    continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        entry.written = True
        op_str = "[yellow]modified[/]" if operation == "modify" else "[green]created[/]"
        print_fn(f"  {op_str}  {rel}")

    return result
