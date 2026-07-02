"""AST-based symbol index for Python and TypeScript/JavaScript codebases.

Provides an exact, fast lookup of functions, classes, and their signatures
without vector search — complements RepoIndex (semantic) with structural data.

Usage::

    from antcrew.core.symbol_index import SymbolIndex

    idx = SymbolIndex.build(["src/", "lib/"])
    print(idx.summary())
    # → "42 functions, 8 classes across 15 files"

    print(idx.context_for(["hash_password", "AuthService"]))
    # → concise snippet listing found symbols for LLM injection

    hits = idx.query_function("hash_password")
    # → [FunctionSymbol(name='hash_password', file_path='src/auth.py', ...)]
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_PYTHON_EXTS = {".py", ".pyw"}
_TS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_MAX_CONTEXT_CHARS = 4_000  # cap for LLM injection


# ---------------------------------------------------------------------------
# Symbol data-classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FunctionSymbol:
    name: str
    module: str          # dotted module path derived from file_path
    file_path: str
    signature: str       # "(arg1, arg2) -> return_type"
    docstring: str
    line: int
    is_method: bool = False
    parent_class: str = ""


@dataclass(slots=True)
class ClassSymbol:
    name: str
    module: str
    file_path: str
    bases: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    docstring: str = ""
    line: int = 0


# ---------------------------------------------------------------------------
# SymbolIndex
# ---------------------------------------------------------------------------

class SymbolIndex:
    """Searchable index of Python symbols extracted via ``ast`` (no LLM)."""

    def __init__(self) -> None:
        self._functions: dict[str, list[FunctionSymbol]] = {}
        self._classes: dict[str, list[ClassSymbol]] = {}
        self._file_count: int = 0

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, paths: list[str | Path]) -> "SymbolIndex":
        """Scan *paths* (files or directories) and return a populated index.

        Supports Python (.py, .pyw) and TypeScript/JavaScript
        (.ts, .tsx, .js, .jsx, .mjs, .cjs) files.
        """
        idx = cls()
        for p in paths:
            root = Path(p)
            if root.is_file():
                idx._index_file(root, root.parent)
            elif root.is_dir():
                for src_file in root.rglob("*"):
                    if src_file.suffix.lower() in (_PYTHON_EXTS | _TS_EXTS):
                        idx._index_file(src_file, root)
        return idx

    def _index_file(self, path: Path, root: Path) -> None:
        suffix = path.suffix.lower()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("symbol_index: skipping %s — %s", path, exc)
            return

        self._file_count += 1
        module = _path_to_module(path, root)

        if suffix in _PYTHON_EXTS:
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                log.debug("symbol_index: parse error %s — %s", path, exc)
                self._file_count -= 1
                return
            _Visitor(module, str(path), self).visit(tree)
        elif suffix in _TS_EXTS:
            _index_ts(source, module, str(path), self)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query_function(self, name: str) -> list[FunctionSymbol]:
        """Return all functions/methods named *name* across the index."""
        return list(self._functions.get(name, []))

    def query_class(self, name: str) -> list[ClassSymbol]:
        """Return all classes named *name* across the index."""
        return list(self._classes.get(name, []))

    def all_function_names(self) -> list[str]:
        return sorted(self._functions.keys())

    def all_class_names(self) -> list[str]:
        return sorted(self._classes.keys())

    # ------------------------------------------------------------------
    # LLM context helpers
    # ------------------------------------------------------------------

    def context_for(self, topics: list[str], *, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
        """Return a compact snippet about *topics* for injection into an agent prompt.

        *topics* can be function names, class names, or file basenames (without
        extension).  Only symbols that fuzzy-match a topic are included.

        Each line is prefixed with the language detected from the file extension
        so agents never confuse Python imports with TypeScript ESM imports.
        """
        lines: list[str] = []
        lower_topics = {t.lower() for t in topics}

        for name, syms in self._functions.items():
            if any(t in name.lower() or name.lower() in t for t in lower_topics):
                for s in syms:
                    lang = _ext_to_lang(s.file_path)
                    location = f"{s.file_path}:{s.line}"
                    lines.append(f"  [{lang}] def {name}{s.signature}  # {location}")

        for name, syms in self._classes.items():
            if any(t in name.lower() or name.lower() in t for t in lower_topics):
                for s in syms:
                    lang = _ext_to_lang(s.file_path)
                    location = f"{s.file_path}:{s.line}"
                    methods = ", ".join(s.methods[:5])
                    lines.append(f"  [{lang}] class {name}  # {location}  methods=[{methods}]")

        if not lines:
            return ""
        block = "Codebase symbols (AST-extracted):\n" + "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars] + "\n  ...[truncated]"
        return block + "\n\n"

    def summary(self) -> str:
        n_fn = sum(len(v) for v in self._functions.values())
        n_cls = sum(len(v) for v in self._classes.values())
        return f"{n_fn} functions, {n_cls} classes across {self._file_count} files"

    # ------------------------------------------------------------------
    # Internal registration (called by _Visitor)
    # ------------------------------------------------------------------

    def _add_function(self, sym: FunctionSymbol) -> None:
        self._functions.setdefault(sym.name, []).append(sym)

    def _add_class(self, sym: ClassSymbol) -> None:
        self._classes.setdefault(sym.name, []).append(sym)


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _Visitor(ast.NodeVisitor):
    def __init__(self, module: str, file_path: str, idx: SymbolIndex) -> None:
        self._module = module
        self._file_path = file_path
        self._idx = idx
        self._current_class: str = ""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [_name_of(b) for b in node.bases]
        methods = [
            n.name for n in ast.walk(node)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and not n.name.startswith("__")
        ]
        doc = ast.get_docstring(node) or ""
        sym = ClassSymbol(
            name=node.name,
            module=self._module,
            file_path=self._file_path,
            bases=bases,
            methods=methods,
            docstring=doc[:120],
            line=node.lineno,
        )
        self._idx._add_class(sym)
        old = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        sig = _build_signature(node)
        doc = ast.get_docstring(node) or ""
        sym = FunctionSymbol(
            name=node.name,
            module=self._module,
            file_path=self._file_path,
            signature=sig,
            docstring=doc[:120],
            line=node.lineno,
            is_method=bool(self._current_class),
            parent_class=self._current_class,
        )
        self._idx._add_function(sym)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXT_LANG: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".java": "Java", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
}


def _ext_to_lang(file_path: str) -> str:
    return _EXT_LANG.get(Path(file_path).suffix.lower(), "unknown")


def _path_to_module(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return "?"


def _build_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []

    def _ann(a: ast.arg) -> str:
        return f": {ast.unparse(a.annotation)}" if a.annotation else ""

    all_args = args.args
    defaults_offset = len(all_args) - len(args.defaults)

    for i, a in enumerate(all_args):
        default_idx = i - defaults_offset
        part = a.arg + _ann(a)
        if default_idx >= 0:
            part += f" = {ast.unparse(args.defaults[default_idx])}"
        parts.append(part)

    if args.vararg:
        parts.append(f"*{args.vararg.arg}" + _ann(args.vararg))
    for a in args.kwonlyargs:
        parts.append(a.arg + _ann(a))
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}" + _ann(args.kwarg))

    ret = ""
    if node.returns:
        ret = f" -> {ast.unparse(node.returns)}"

    return f"({', '.join(parts)}){ret}"


# ---------------------------------------------------------------------------
# TypeScript / JavaScript regex indexer
# ---------------------------------------------------------------------------

# Patterns for exported declarations
_TS_EXPORT_FN = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_TS_EXPORT_ARROW = re.compile(
    r"^export\s+(?:const|let|var)\s+(\w+)\s*(?::\s*\([^)]*\)\s*=>\s*\S+)?\s*=\s*(?:async\s*)?\(",
    re.MULTILINE,
)
_TS_EXPORT_CLASS = re.compile(
    r"^export\s+(?:default\s+)?(?:abstract\s+)?class\s+(\w+)",
    re.MULTILINE,
)
_TS_EXPORT_INTERFACE = re.compile(
    r"^export\s+(?:default\s+)?interface\s+(\w+)",
    re.MULTILINE,
)
_TS_EXPORT_TYPE = re.compile(
    r"^export\s+type\s+(\w+)",
    re.MULTILINE,
)
_TS_METHOD = re.compile(
    r"^\s{2,}(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)


def _index_ts(source: str, module: str, file_path: str, idx: "SymbolIndex") -> None:
    """Regex-based extraction of exported symbols from TypeScript/JavaScript."""
    current_class = ""

    # Exported functions
    for m in _TS_EXPORT_FN.finditer(source):
        name = m.group(1)
        params = m.group(2).strip()
        sig = f"({params})"
        line = source[: m.start()].count("\n") + 1
        idx._add_function(FunctionSymbol(
            name=name, module=module, file_path=file_path,
            signature=sig, docstring="", line=line,
        ))

    # Exported arrow functions
    for m in _TS_EXPORT_ARROW.finditer(source):
        name = m.group(1)
        line = source[: m.start()].count("\n") + 1
        idx._add_function(FunctionSymbol(
            name=name, module=module, file_path=file_path,
            signature="(...)", docstring="", line=line,
        ))

    # Exported classes
    for m in _TS_EXPORT_CLASS.finditer(source):
        name = m.group(1)
        line = source[: m.start()].count("\n") + 1
        # Collect method names in approximate class body (next 3000 chars)
        body = source[m.end(): m.end() + 3000]
        methods = [mm.group(1) for mm in _TS_METHOD.finditer(body)
                   if not mm.group(1).startswith("_")]
        idx._add_class(ClassSymbol(
            name=name, module=module, file_path=file_path,
            methods=methods[:20], line=line,
        ))

    # Exported interfaces
    for m in _TS_EXPORT_INTERFACE.finditer(source):
        name = m.group(1)
        line = source[: m.start()].count("\n") + 1
        idx._add_class(ClassSymbol(
            name=name, module=module, file_path=file_path,
            line=line,
        ))

    # Exported type aliases
    for m in _TS_EXPORT_TYPE.finditer(source):
        name = m.group(1)
        line = source[: m.start()].count("\n") + 1
        idx._add_class(ClassSymbol(
            name=name, module=module, file_path=file_path,
            line=line,
        ))
