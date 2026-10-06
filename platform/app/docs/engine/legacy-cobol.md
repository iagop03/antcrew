# Legacy / COBOL Support

antcrew has first-class support for organisations running COBOL on IBM AS/400 (iSeries) mainframes. You can index COBOL programs as documentation, add AI capabilities without rewriting them, connect to DB2 for i databases, and translate programs to Python/Java/Go.

```bash
pip install "antcrew[docs,legacy]"
```

---

## What's included

| Feature | Package | CLI command |
|---|---|---|
| Parse `.cbl`, `.cob`, `.cpy` files into the doc index | `antcrew[docs]` | `antcrew engine --docs-dir` |
| `org_type: legacy` in schema — enables COBOL-aware routing | `antcrew[docs]` | — |
| `AS400Connector` — query DB2 for i tables | `antcrew[legacy]` | — |
| `COBOLAugment` — add AI to COBOL without rewriting | `antcrew[legacy]` | `antcrew augment-cobol` |
| COBOL → Python/Java/Go structural translation | `polytranslate` | — |
| Java → COBOL (LLM-based, standards-aware) | `polytranslate` | `antcrew java-to-cobol` |

---

## COBOL documentation indexing

### Supported file types

| Extension | Type | Parser |
|---|---|---|
| `.cbl`, `.cob` | COBOL program | `CobolParser` |
| `.cpy`, `.copy` | Copybook | `CobolParser` (copybook mode) |

The COBOL parser handles both **fixed-format** (cols 1-6 sequence, col 7 indicator) and **free-format** COBOL. It extracts:

- `PROGRAM-ID`, `AUTHOR`, `DATE-WRITTEN`
- Data items: WORKING-STORAGE, FILE, LINKAGE sections — level, name, PIC clause
- Paragraph names from PROCEDURE DIVISION
- `COPY` and `CALL` statements
- Division structure

Each parsed program becomes a human-readable summary + structured metadata in the doc index, available for semantic search and agent context injection.

### Schema configuration for legacy organisations

Set `org_type: legacy` and configure `if_legacy.cobol_support` in your schema YAML:

```yaml
documentation_schema:
  org_name: Acme Corp
  org_type: legacy          # legacy | microservices | mixed

  if_legacy:
    cobol_support:
      enabled: true
      as400_connection: false   # true if you use AS400Connector
      copybook_parsing: true    # parse .cpy/.copy as COBOL
      batch_job_docs: true      # treat COBOL programs as batch-job docs

  document_types:
    - id: cobol_program
      name: COBOL Batch Program
      category: technical
      parser: cobol

    - id: copybook
      name: COBOL Copybook
      category: technical
      parser: cobol

    - id: jcl_procedure
      name: JCL Procedure
      category: operational
      parser: text

  path_rules:
    - prefix: "cobol/"
      doc_type: cobol_program
    - prefix: "copybooks/"
      doc_type: copybook
    - prefix: "jcl/"
      doc_type: jcl_procedure

  query_hints:
    - pattern: "batch|orden|order processing|ORDPRC"
      doc_types: [cobol_program]
    - pattern: "copybook|estructura|field layout"
      doc_types: [copybook]
```

```python
from antcrew_engine.documentation.schema import DocumentationSchemaRegistry

reg = DocumentationSchemaRegistry()
reg.load_from_file("schema.yaml")

print(reg.is_legacy)       # True
print(reg.cobol_support)   # CobolSupport(enabled=True, as400_connection=False, ...)
```

---

## AS400Connector

Read-only access to IBM DB2 for i (AS/400) databases via ODBC. Requires `pip install antcrew[legacy]` which adds `pyodbc`.

### Prerequisites

- IBM iSeries Access ODBC driver installed on the machine running antcrew
- ODBC DSN configured for your AS/400 system, **or** a full connection string

### Usage

```python
from antcrew.integrations.as400 import AS400Connector

conn = AS400Connector(
    dsn="MY_AS400_DSN",       # pre-configured ODBC DSN
    username="MYUSER",
    password="MYPASS",
    library="ORDLIB",         # default schema/library
)

# Column metadata for a table
schema = conn.get_schema("ORDLIB.ORDHDRF")
for col in schema.columns:
    print(f"{col.name:30} {col.data_type}({col.length})")

# COBOL copybook layout derived from the schema
print(conn.get_copybook("ORDLIB.ORDHDRF"))

# First N rows as a list of dicts (read-only)
rows = conn.sample_data("ORDLIB.ORDHDRF", limit=5)
```

```python
# Context manager — closes connection automatically
with AS400Connector(dsn="MY_AS400_DSN", username="U", password="P") as conn:
    schema = conn.get_schema("CUSTLIB.CUSTMST")
```

### Connection string form

```python
conn = AS400Connector(
    connection_string="Driver={IBM i Access ODBC Driver};System=192.168.1.10;",
    username="MYUSER",
    password="MYPASS",
)
```

### DB2-to-COBOL PIC mapping

`get_copybook()` maps DB2 column types to COBOL PIC clauses:

| DB2 type | PIC clause |
|---|---|
| `CHAR(n)` / `VARCHAR(n)` | `X(n)` |
| `DECIMAL(p,s)` | `9(p-s)V9(s)` |
| `INTEGER` | `S9(9) COMP-4` |
| `SMALLINT` | `S9(4) COMP-4` |
| `BIGINT` | `S9(18) COMP-4` |
| `DATE` | `X(10)` |
| `TIMESTAMP` | `X(26)` |

---

## COBOLAugment — add AI without rewriting

`COBOLAugment` analyses a COBOL program and generates three artefacts:

1. **Python AI wrapper** — a class with `process()` + AI enrichment hooks, bridging to your COBOL via REST, MQ, or batch file exchange
2. **COBOL bridge caller** — a COBOL program (`PROGRAM-ID. XXXAI`) that calls `ANTCREW-SEND` to invoke the Python service
3. **Deployment guide** — step-by-step instructions for three integration patterns (REST adapter, IBM MQ, batch file exchange)

### CLI

```bash
antcrew augment-cobol ORDPRC.cbl --requirement "Add ML fraud scoring to order processing"
```

Options:

| Flag | Description |
|---|---|
| `--requirement` / `-r` | What AI capability to add (required) |
| `--model` / `-m` | LLM to use for wrapper generation. Omit for a static template. |
| `--output` / `-o` | Output directory. Defaults to the COBOL file's directory. |
| `--guide` | Also print the deployment guide to stdout. |
| `--dry-run` | Preview output without writing files. |

### Python API

```python
from antcrew.augment.cobol import COBOLAugment

aug = COBOLAugment(llm=my_llm)   # llm is optional — omit for static templates
result = aug.augment("ORDPRC.cbl", requirement="Add ML fraud scoring")

print(result.python_wrapper)     # Python class with AI hooks
print(result.cobol_caller)       # COBOL bridge program
print(result.deployment_guide)   # Markdown deployment instructions

# Write to disk
from pathlib import Path
Path("ordprc_ai.py").write_text(result.python_wrapper)
Path("ORDPRCAI.cbl").write_text(result.cobol_caller)
Path("ORDPRC_deployment_guide.md").write_text(result.deployment_guide)
```

### Analysis only

Use `COBOLAnalyzer` directly if you only need the structural analysis:

```python
from antcrew.augment.cobol import COBOLAnalyzer

analysis = COBOLAnalyzer().analyze("ORDPRC.cbl")

print(analysis.program_id)         # "ORDPRC"
print(analysis.paragraphs)         # ["MAIN-PARA", "VALIDATE-ORDER", ...]
print(analysis.working_storage_fields)  # [DataItem(level=1, name="WS-ORDER-ID", ...)]
print(analysis.external_calls)     # [ExternalCall(kind="CALL", target="VALDATE"), ...]
print(analysis.summary())          # human-readable overview
```

### Integration patterns

The generated deployment guide covers three patterns:

**Option A — REST adapter (recommended for AS/400)**

```
COBOL ORDPRC  →  ORDPRCAI.cbl  →  HTTP POST /process  →  OrdprcAI (Python)
```

The COBOL bridge calls `ANTCREW-SEND` (a C or RPG subprogram that wraps IBM HTTP Client). The Python service runs on any Linux/Windows server accessible from the AS/400.

**Option B — IBM MQ trigger**

The COBOL program writes to an MQ queue instead of calling HTTP. The Python service listens via `ibm-mq`. Reliable, asynchronous, no synchronous coupling.

**Option C — Batch file exchange**

COBOL writes input to a flat file; Python reads and processes it overnight. Suitable for batch jobs with no real-time requirement.

---

## polytranslate — full structural translation

For teams that want to migrate COBOL programs to Python/Java/Go rather than augmenting them:

```bash
pip install polytranslate
```

```python
from polytranslate.languages.cobol import CobolParser
from polytranslate.targets.python import PythonGenerator

ast = CobolParser().parse_file("ORDPRC.cbl")
files = PythonGenerator().generate(ast)

for f in files:
    f.write("./output")
    # Produces: ordprc_model.py, ordprc_logic.py, ordprc_runner.py
```

The translation is **structural** — every data item and paragraph is mapped to an equivalent Python construct, producing a working skeleton for developer review. It does not evaluate COBOL expressions or simulate COBOL runtime behaviour.

See the [polytranslate repository](https://github.com/iagop03/polytranslate) for the full pipeline architecture and how to add new target languages.

---

## Java → COBOL (LLM-based)

`JavaToCOBOLTranslator` uses an LLM to produce COBOL code from Java source, optionally learning naming conventions from an existing COBOL file or a standards document. Output is automatically normalised and structurally validated after every translation.

```bash
pip install polytranslate anthropic   # or: pip install "antcrew[java-to-cobol]"
```

### LLM configuration

`from_env()` detects the LLM provider automatically. Priority order:

| Env var | Provider | Default model |
|---|---|---|
| `KEYBRIDGE_URL` + `KEYBRIDGE_TOKEN` | KeyBridge proxy (recommended for teams) | `claude-sonnet-5` |
| `ANTHROPIC_API_KEY` | Anthropic direct | `claude-sonnet-5` |
| `OPENAI_API_KEY` | OpenAI direct | `gpt-4o` |
| `DEEPSEEK_API_KEY` | DeepSeek (OpenAI-compatible) | `deepseek-chat` |
| `GROQ_API_KEY` | Groq (OpenAI-compatible) | `llama-3.3-70b-versatile` |

```python
from polytranslate.translators.java_to_cobol import JavaToCOBOLTranslator

translator = JavaToCOBOLTranslator.from_env()           # auto-detect
translator = JavaToCOBOLTranslator.from_env("claude-opus-5")  # override model
translator = JavaToCOBOLTranslator(llm=my_langchain_llm)      # bring your own
```

When called from **antcrew**, the translator receives the LLM from `build_llm()` — no separate configuration needed.

### Translation pipeline

A single `translate()` call runs the full pipeline:

```python
translator = JavaToCOBOLTranslator.from_env()
translator.load_standards("CLAIMS.cbl")

cobol = translator.translate(open("OrderProcessor.java").read())
```

1. **Chunking** — files over 150 lines are split by method, translated independently, then merged
2. **LLM call** — prompt built with or without standards; 60 s hard timeout
3. **Normalisation** — `COBOLNormalizer` runs automatically: variable prefixes, paragraph names, section order, column formatting
4. **Validation** — structural checks returned by `translator.validate(cobol)`

Skip normalisation with `translate(java_code, normalize=False)`.

### Standards learning

```python
translator.load_standards("CLAIMS.cbl")          # learn from existing COBOL
translator.load_standards("COBOL_STANDARDS.md")  # or from a doc
```

Standards are cached in `~/.antcrew/standards/` keyed by filename + mtime — repeated calls on the same file are instant. A `templates/COBOL_STANDARDS_TEMPLATE.md` ships with polytranslate as a starting point.

### Structural validation

`validate()` checks the output for the most common LLM mistakes:

```python
result = translator.validate(cobol)
# result.valid      → bool
# result.errors     → ["PROCEDURE DIVISION is missing", …]
# result.warnings   → ["STOP RUN not found", …]
print(result.summary())
```

Checks: four divisions present, `PROGRAM-ID` set, `STOP RUN` / `GOBACK` present, no markdown fences leaking into output, no names exceeding 30 characters.

### Refining bad output

If the first translation is wrong, use `refine()` instead of re-translating from scratch:

```python
cobol_v2 = translator.refine(
    java_code=open("OrderProcessor.java").read(),
    current_cobol=open("cobol_output/OrderProcessor.cbl").read(),
    feedback="The VALIDATE-ORDER paragraph is missing the date-range check from validateOrder()",
)
```

The LLM receives the original Java, the previous COBOL, and the feedback in a single prompt and produces a targeted fix.

### CLI (via antcrew)

```bash
antcrew java-to-cobol OrderProcessor.java
antcrew java-to-cobol OrderProcessor.java --standards CLAIMS.cbl
antcrew java-to-cobol OrderProcessor.java --standards COBOL_STANDARDS.md -o ./output
antcrew java-to-cobol OrderProcessor.java --refine out.cbl --feedback "missing date check"
antcrew java-to-cobol OrderProcessor.java --dry-run
```

| Flag | Description |
|---|---|
| `--standards` / `-s` | COBOL file or standards doc to learn naming from |
| `--output` / `-o` | Output directory (default: `./cobol_output/`) |
| `--model` / `-m` | LLM to use (default: `claude`) |
| `--refine` / `-r` | Existing `.cbl` to refine instead of translating from scratch |
| `--feedback` / `-f` | Feedback text for `--refine` mode |
| `--no-normalize` | Skip COBOLNormalizer pass |
| `--dry-run` | Print generated COBOL without writing files |

The CLI prints validation results (errors/warnings/OK) after every translation. Files larger than 1 MB are rejected.

### CLI (standalone — polytranslate only)

```bash
pip install "polytranslate[cli]"

# Translate one file
polytranslate translate java-to-cobol OrderProcessor.java
polytranslate translate java-to-cobol OrderProcessor.java -s CLAIMS.cbl -o ./output
polytranslate translate java-to-cobol OrderProcessor.java --refine out.cbl --feedback "..."

# Translate many files in parallel (batch)
polytranslate translate java-to-cobol-batch "src/**/*.java" -s CLAIMS.cbl -o ./cobol/
polytranslate translate java-to-cobol-batch "*.java" --workers 5

# Extract standards from an existing COBOL file or doc
polytranslate extract-standards CLAIMS.cbl
polytranslate extract-standards CLAIMS.cbl -o COBOL_STANDARDS.md
```

`java-to-cobol-batch` accepts a glob pattern and translates all matched `.java` files concurrently (default 3 workers). Standards are loaded once and reused for all files — the cache means the extractor only runs once per standards file. Exits with code 1 if any file fails, so it works in CI pipelines.

`extract-standards` produces a filled `COBOL_STANDARDS.md` you can share with your team and pass back as `--standards` on future translations.

### Using as an agent tool

`JavaToCOBOLTool` wraps the translator as an antcrew `BaseTool` so any agent can call it mid-task:

```python
from antcrew.tools import JavaToCOBOLTool

tool = JavaToCOBOLTool()          # auto-detects LLM from env
tool = JavaToCOBOLTool(llm=my_llm, standards_file="CLAIMS.cbl")

agent = MigrationAgent(llm, tools=[tool])
```

The tool accepts JSON input with `java_code`, optional `standards_file`, and optional `feedback` + `current_cobol` for refinement. It returns the COBOL string prefixed with any validation warnings.

### COBOLNormalizer

Normalisation runs automatically inside `translate()`. To run it manually on existing COBOL:

```python
from polytranslate.utils.cobol_normalizer import COBOLNormalizer

normalizer = COBOLNormalizer()
clean_cobol = normalizer.normalize(raw_cobol)

# With custom prefixes:
normalizer = COBOLNormalizer(standards={
    "var_prefixes": {"working_storage": "WRK", "constants": "CST"},
})
```

Four passes in sequence:

1. **Variable renaming** — `orderAmount` → `WS-ORDER-AMOUNT`
2. **Paragraph renaming** — `processOrder.` → `PROCESS-ORDER.`
3. **Section reorganization** — WC- constants before WS- variables in WORKING-STORAGE
4. **Formatting** — COBOL column layout (divisions col 1, data items col 8, statements col 12)

---

## Choosing the right tool

| Scenario | Recommended tool |
|---|---|
| Index COBOL programs in the doc system | `antcrew[docs]` + `org_type: legacy` |
| Query DB2 for i schema from Python | `AS400Connector` |
| Keep COBOL running, add AI logic alongside | `antcrew augment-cobol` |
| Migrate COBOL to Python/Java/Go | `polytranslate` |
| Translate Java to COBOL (standards-aware) | `JavaToCOBOLTranslator` / `antcrew java-to-cobol` |
| Full AI-driven rewrite with agent team | `antcrew run --team CodeMigrationTeam` |
