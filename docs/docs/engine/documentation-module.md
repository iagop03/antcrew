# Documentation Module

The Documentation Module lets the engine index your project's existing documentation and inject relevant context into every capability's LLM prompt — so the code generator knows your auth requirements, the architect sees your ADRs, and the bug fixer reads your error-handling policy.

```bash
pip install "antcrew[docs]"   # adds python-docx, pypdf, networkx, gitpython, boto3
```

---

## Quick start

### CLI

```bash
antcrew engine "Add user authentication" \
  --schema ./docs/schema.yaml \
  --docs-dir ./docs \
  --tech Python --tech FastAPI \
  --output ./my-api
```

`--schema` points to your [schema file](#schema-file) (optional — runs with defaults if omitted).  
`--docs-dir` is the directory to index. The engine uploads all supported files, then injects relevant snippets into each capability before it calls the LLM.

### Python API

```python
from antcrew_engine.documentation import DocumentationManager

mgr = DocumentationManager(schema_path="docs/schema.yaml")
mgr.bulk_upload("./docs")

results = mgr.search("JWT authentication", top_k=5)
context = mgr.get_context_for_agent("BackendDev", "login flow")
```

---

## Schema file

A schema.yaml describes your doc types and tells the engine which type matters for which capability.

```yaml
documentation_schema:
  org_name: Acme Corp

  document_types:
    - id: srs
      name: Software Requirements Specification
      category: functional
      purpose: Define what the system must do
      parser: markdown          # markdown | text | docx | pdf | jira
      related_to: [design, adr]

    - id: design
      name: Technical Design
      category: technical
      purpose: Architecture and implementation decisions
      parser: markdown
      related_to: [srs]

    - id: adr
      name: Architecture Decision Record
      category: technical
      purpose: Rationale for major decisions
      parser: markdown

  agent_hints:
    Architect:
      - "For requirements: search Software Requirements Specification (srs)"
      - "For prior decisions: search Architecture Decision Record (adr)"
    CodeGenerator:
      - "For requirements: search Software Requirements Specification (srs)"
      - "For design details: search Technical Design (design)"
```

The engine reads `agent_hints` to decide which doc types to query per capability. If no hints match the capability class name, it falls back to a generic search across all indexed documents.

### Intent-based routing with `query_hints`

`query_hints` route search queries to the right doc types based on the intent expressed in the query — without requiring the caller to know which type to search.

```yaml
documentation_schema:
  query_hints:
    - pattern: "crear tabla|create table|nueva tabla"
      doc_types: [procedure]

    - pattern: "componente|component|implementar|implement"
      doc_types: [functional_spec, technical_design]

    - pattern: "error|excepción|exception|handling"
      doc_types: [technical_design, adr]
```

Each `pattern` is a **case-insensitive regex** (or a plain substring if it contains no regex metacharacters). When a query matches, the listed `doc_types` are searched first; their results are ranked above the generic fallback. Multiple hints can match the same query — their doc_types are merged in order.

```python
# Without hints: generic search
results = mgr.search("how to create a user table")

# With the query_hint above: procedure docs come first, generic results fill remaining slots
results = mgr.search("how to create a user table")   # same call, automatic routing
```

Explicit `doc_type=` / `category=` filters always bypass hint routing.

### Path-based classification with `path_rules`

When indexing documents that already exist in storage (S3 or local) and have no antcrew metadata sidecar, `path_rules` map storage path prefixes or suffixes to doc types.

```yaml
documentation_schema:
  path_rules:
    - prefix: "procedures/"
      doc_type: procedure

    - prefix: "specs/"
      doc_type: functional_spec

    - suffix: ".jira.json"
      doc_type: jira_ticket

    - prefix: "adrs/"
      suffix: ".md"
      doc_type: adr
```

Rules are evaluated in schema order; **first match wins**. Both `prefix` and `suffix` can be combined in one rule — the file must satisfy both. Matching is case-insensitive.

Detection order in `index_from_storage()`:

| Priority | Source |
|----------|--------|
| 1 | antcrew metadata sidecar (`.meta/{doc_id}.json`) |
| 2 | S3 native user-metadata via `HeadObject` (`doc-type` key) |
| 3 | `path_rules` from schema |
| 4 | Filename convention (`{doc_type}.{project}.{ext}`) |
| 5 | Extension fallback (`.md → markdown`, etc.) |

### File naming convention

Files can auto-detect their type using the pattern `{doc_type}.{project}.{ext}`:

```
docs/
  srs.claims-processing.md      → doc_type=srs, project=claims-processing
  design.auth-service.md        → doc_type=design, project=auth-service
  adr.0042-jwt-choice.md        → doc_type=adr
```

Files that don't follow the convention are classified by extension (`.md → markdown`, `.docx → docx`, `.pdf → pdf`, `.json → jira`).

---

## Supported parsers

| Parser | Extensions | Notes |
|--------|-----------|-------|
| `markdown` | `.md`, `.markdown` | Extracts headings as sections, counts words |
| `text` | `.txt` | Plain text; treats the whole file as one section |
| `docx` | `.docx`, `.doc` | Requires `python-docx` |
| `pdf` | `.pdf` | Requires `pypdf`; extracts page text |
| `jira` | `.json` | Parses Jira ticket JSON exports; falls back to plain text |
| `cobol` | `.cbl`, `.cob`, `.cpy`, `.copy` | Extracts PROGRAM-ID, data items, paragraphs, COPY/CALL statements |

### COBOL parser

The COBOL parser (`CobolParser`) handles both fixed-format (cols 1-6 sequence, col 7 indicator) and free-format COBOL. It produces a human-readable Markdown summary of the program structure plus structured metadata:

```python
from antcrew_engine.documentation.parsers.cobol import CobolParser

doc = CobolParser().parse("ORDPRC.cbl")
print(doc.content)           # human-readable summary: divisions, data items, paragraphs
print(doc.metadata["program_id"])      # "ORDPRC"
print(doc.metadata["paragraphs"])      # ["MAIN-PARA", "VALIDATE-ORDER", ...]
print(doc.metadata["called_programs"]) # ["VALDATE", "ERRHDLR"]
print(doc.metadata["copybooks"])       # ["COMMONLIB", "CUSTRECORD"]
```

To enable COBOL parsing in the documentation module, set `org_type: legacy` (or add `parser: cobol` to any document type) in your schema:

```yaml
documentation_schema:
  org_type: legacy
  if_legacy:
    cobol_support:
      copybook_parsing: true
```

→ See [Legacy / COBOL Support](legacy-cobol.md) for the full reference.

---

## Storage backends

| Backend | Config | Notes |
|---------|--------|-------|
| `local` *(default)* | `path: ./documentation` | Files stored in a local directory |
| `git` | `path: ./documentation` | Commit each document as a git blob; requires `gitpython` |
| `s3` | `bucket`, `prefix`, `region` | Requires `boto3` |

```python
mgr = DocumentationManager(
    schema_path="schema.yaml",
    storage_type="s3",
    storage_config={
        "bucket": "my-docs",
        "prefix": "v2/",
        "region": "eu-west-1",
        # optional — falls back to env vars / IAM role when omitted
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "...",
    },
)
```

### Indexing existing storage files

`index_from_storage()` reads every document already present in the configured backend and indexes them in-process without re-uploading. Use it to integrate a pre-existing S3 bucket, a git repo, or a local directory tree:

```python
mgr = DocumentationManager(
    schema_path="schema.yaml",
    storage_type="s3",
    storage_config={"bucket": "company-docs", "prefix": "project-a/"},
)

# Index all existing files — uses path_rules + S3 metadata for type detection
indexed = mgr.index_from_storage()
print(f"Indexed {len(indexed)} documents")

results = mgr.search("authentication requirements", top_k=5)
```

For files without an antcrew metadata sidecar, configure `path_rules` in your schema so the engine knows which types they belong to (see [Path-based classification](#path-based-classification-with-path_rules) above).

#### S3 native user-metadata

If your existing S3 objects already carry user-defined metadata, the engine reads `doc-type` (or `doc_type`) from `HeadObject` as the second detection layer:

```python
# When saving files outside antcrew:
s3.put_object(
    Bucket="company-docs",
    Key="procedures/onboarding.md",
    Body=content,
    Metadata={"doc-type": "procedure"},
)
```

---

## Search and semantic index

By default the index uses **keyword search** (TF-IDF-like term overlap, no dependencies). Install ChromaDB to upgrade to **semantic search**:

```bash
pip install chromadb
```

When ChromaDB is present, documents are embedded on upload and searched by cosine similarity. The upgrade is transparent — no code changes needed.

```python
results = mgr.search("JWT authentication requirements", top_k=5)
# results: list of {"id": ..., "content": ..., "metadata": ..., "score": ...}

# Explicit filter by doc type or category (bypasses query_hints routing)
results = mgr.search_by_type("login flow", "srs", top_k=3)
results = mgr.search_by_category("authentication", "functional", top_k=5)

# When query_hints are configured, plain search() routes automatically:
results = mgr.search("how to create a user table")     # → procedure docs first
results = mgr.search("implement authentication component")  # → functional_spec, technical_design
```

---

## Knowledge graph

The module maintains a relation graph between documents based on `related_to` in the schema. Install NetworkX to enable full graph algorithms (cycle detection, subgraph traversal):

```bash
pip install networkx
```

Without NetworkX, the graph falls back to an adjacency dict that supports `get_neighbors` and basic traversal.

```python
related = mgr.get_related_documents("srs/my-spec.md")
# → [{"id": "design/my-design.md", "doc_type": "design", ...}]
```

---

## Agent and team integration

The Documentation Module works for **all paths** — agents, teams, and engine capabilities.

### Teams (DevTeam, FullStackTeam, etc.)

```python
from antcrew import DevTeam, DocumentationManager
from antcrew.config import build_llm

llm = build_llm("claude")
mgr = DocumentationManager(schema_path="schema.yaml")
mgr.bulk_upload("./docs")

team = DevTeam(llm=llm)
team.set_documentation(mgr)      # propagates to all agents in the team

result = team.run("Add user authentication with JWT")
```

Every agent's `system()` call automatically prepends relevant documentation to the user message — no per-agent code changes needed.

### Individual agents

```python
from antcrew import DirectAgent, DocumentationManager

agent = DirectAgent(llm=llm)
agent.set_documentation(mgr)

result = agent.run({"request": "Implement JWT login"})
```

### Engine capabilities (BaseExecutor)

Engine capabilities also have `set_documentation()` and `_doc_context()`. The CLI wires this automatically via `--docs-dir`. For custom executors:

```python
from antcrew_engine.capabilities.base import BaseExecutor
from antcrew_engine.engine import CapabilityDescriptor, CapabilityResult

class SecurityChecker(BaseExecutor):
    descriptor = CapabilityDescriptor(
        name="security_checker",
        description="Reviews code against project security policy.",
        conditions_produced=["security_verified"],
    )

    def _run(self, store, goal):
        doc_context = self._doc_context("security requirements threat model OWASP")
        system = "You are a security reviewer."
        user = f"{doc_context}\n\nReview:\n\n{goal.description}"
        review = self._call(system, user)
        # ...
```

### How automatic injection works

When a `DocumentationManager` is attached, `_inject_documentation(user)` is called inside every `system()` / `system_with_images()` call. It:

1. Calls `get_context_for_agent(agent_name, user_message)` using the `agent_hints` from the schema
2. Falls back to `search(user_message, top_k=3)` if no hints match the agent name
3. Prepends a `## Relevant documentation` block to the user message
4. Returns the original message unchanged when no docs are found or on any error

---

## Statistics and validation

```python
stats = mgr.get_statistics()
# {"total_documents": 12, "by_type": {"srs": 3, "design": 5, ...}, "by_category": {...}, "index": {...}}

report = mgr.validate_against_schema()
# {"present_types": ["srs", "design"], "missing_types": ["adr"], "errors": []}
```

---

## Optional dependencies summary

```toml
# Install only what you need
pip install "antcrew[docs]"          # all doc-related deps
pip install "antcrew[memory]"        # ChromaDB for semantic search
pip install python-docx              # .docx parsing
pip install pypdf                    # .pdf parsing
pip install networkx                 # full knowledge graph
pip install gitpython                # git storage backend
pip install boto3                    # S3 storage backend
```
