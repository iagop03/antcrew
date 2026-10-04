# Release pipeline

The release pipeline turns a set of change-requests into a production-ready package: a signed ZIP with the change log, impact analysis, test evidence, approvals, and an optional ServiceNow Change Request.

```
Release / ReleaseItem   → data model
ImpactAnalyzer          → risk analysis per CR
ApproversConfig         → who must sign off for each risk level
ChangePackager          → assembles the package (Excel + Markdown + emails)
antcrew release         → CLI commands
```

---

## Data model

```python
from antcrew.models.release import Release, ReleaseItem, ReleaseState
```

### `Release`

A named group of change-requests scheduled for deployment.

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique release identifier |
| `name` | `str` | Human-readable name, e.g. `"2026-W40"` |
| `state` | `ReleaseState` | `draft` → `pending_approval` → `approved` / `rejected` → `deployed` |
| `target_date` | `str \| None` | ISO-8601 date |
| `items` | `list[ReleaseItem]` | Change-request lines |
| `notes` | `str` | Optional free-text notes |

**Methods**

```python
release = Release(id="r1", name="2026-W40")

# Add CRs
release.add_item("CR-1234", run_ids=["run-abc"])
release.add_item("CR-1235", origin="vcs_only")   # committed directly, no antcrew run

# State machine
release.submit_for_approval()   # draft → pending_approval
release.approve()               # pending_approval → approved
release.deploy()                # approved → deployed
release.reject()                # → rejected

# Convenience
release.change_refs             # ["CR-1234", "CR-1235"]
release.all_run_ids             # ["run-abc"]
```

### `ReleaseItem`

One change-request line within a release.

| Field | Type | Description |
|---|---|---|
| `release_id` | `str` | Parent release ID |
| `change_ref` | `str` | CR reference, e.g. `CR-1234` |
| `run_ids` | `list[str]` | antcrew run UUIDs that implemented this CR |
| `origin` | `"antcrew" \| "vcs_only"` | Whether it went through antcrew or was committed directly |
| `summary` | `str` | Short description |
| `impact_risk` | `str` | `low` / `medium` / `high` — from `ImpactAnalysis` |

---

## ImpactAnalyzer

Maps VCS changed files to transitive COBOL program impact and assigns a risk level.

```python
from antcrew.augment.impact.analyzer import ImpactAnalyzer
from antcrew.augment.cobol.index import COBOLIndex
```

Requires a [`COBOLIndex`](legacy-cobol.md) built from your codebase.

```python
index = COBOLIndex.build("/opt/repos/myapp/src")

analyzer = ImpactAnalyzer(index)
analysis = analyzer.analyze(
    changed_files=["src/ACCTUPD.cbl", "src/COBR0042.cbl"],
    change_ref="CR-1234",
)

print(analysis.risk_level)          # "high"
print(analysis.risk_reason)         # "DB2 writes in ACCTUPD"
print(analysis.modified_components) # [ImpactedComponent(name="ACCTUPD", ...)]
print(analysis.affected_callers)    # ["MAINPGM", "BATCHJOB"]
print(analysis.db2_tables_touched)  # [{"table": "ACCOUNTS", "op": "UPDATE"}]
print(analysis.as_markdown())       # formatted report
```

### `ImpactAnalysis` fields

| Field | Description |
|---|---|
| `change_ref` | The CR this analysis belongs to |
| `modified_components` | Directly changed programs / copybooks |
| `affected_callers` | Transitive callers of modified programs |
| `affected_copybook_users` | Programs that `COPY` a modified copybook |
| `db2_tables_touched` | Aggregate `{table, op}` list across all programs |
| `risk_level` | `low` / `medium` / `high` |
| `risk_reason` | Human-readable justification |

**Risk heuristics**

| Risk | Condition |
|---|---|
| `high` | DB2 writes (`UPDATE` / `INSERT` / `DELETE`) or > 5 transitive callers |
| `medium` | DB2 reads only, or 2–5 callers |
| `low` | No DB2 access and ≤ 1 caller |

---

## ApproversConfig

Risk-proportional approval routing. Defines who must sign off depending on the highest risk level in a release.

```python
from antcrew.packager.approvers import ApproversConfig, Approver
```

### YAML format

```yaml
# approvers.yaml
approvers:
  - role: qa_lead
    name: "María García"
    email: mgarcia@company.com
    required_if:
      risk: [medium, high]        # required when any CR is medium or high risk

  - role: risk_officer
    name: "John Smith"
    email: jsmith@company.com
    required: always              # always required regardless of risk

  - role: business_owner
    name: "Ana Pérez"
    email: aperez@company.com
    required_if:
      risk: [high]

# Optional: customize Excel columns to match your ServiceNow template
excel_columns:
  - change_ref
  - summary
  - risk_level
  - modified_components
  - db2_tables
  - test_runs
  - approvers
  - target_date
```

### Python API

```python
config = ApproversConfig.from_yaml("approvers.yaml")

# Who needs to sign off for a high-risk release?
required = config.required_for("high")
# → [Approver(role="qa_lead"), Approver(role="risk_officer"), Approver(role="business_owner")]

required = config.required_for("low")
# → [Approver(role="risk_officer")]   # only the always-required one
```

---

## ChangePackager

Assembles the full change package for a release. Outputs an Excel workbook, a Markdown summary, and email drafts for each required approver.

```python
from antcrew.packager import ChangePackager
from antcrew.models.release import Release
from antcrew.packager.approvers import ApproversConfig
from antcrew.trace import TraceLog

packager = ChangePackager(
    release,
    approvers_config=ApproversConfig.from_yaml("approvers.yaml"),
    trace_log=tlog,                              # optional — adds test evidence
    impact_analyses={"CR-1234": analysis},       # optional — adds risk data per CR
)
result = packager.build(output_dir="./pkg/2026-W40")
```

`ChangePackager` requires `pip install antcrew[release]` (adds `openpyxl`).

### `PackageResult`

| Field | Description |
|---|---|
| `excel_path` | Path to the generated `.xlsx` workbook, or `None` if openpyxl not installed |
| `summary_md` | Markdown summary of all CRs with impact and approvers |
| `email_drafts` | `list[{"to", "subject", "body"}]` — one per required approver |
| `output_dir` | Directory where all outputs were written |

### CLI

```bash
# Create or update a release, then package it:
antcrew release create --name "2026-W40" --target 2026-10-11
antcrew release add-cr CR-1234 CR-1235
antcrew release package --output-dir ./pkg/2026-W40

# Submit to ServiceNow (needs servicenow: block in agentteam.yaml):
antcrew release submit --config agentteam.yaml

# Show current release state:
antcrew release status
```

---

## Release HITL chain

When a release has approvers configured, `antcrew release package` can optionally gate on human approval before generating the final signed ZIP:

```python
from antcrew_engine.capabilities import HitlReviewer
from antcrew.packager.approvers import ApproversConfig
from antcrew.trace import TraceLog

config = ApproversConfig.from_yaml("approvers.yaml")
tlog = TraceLog("trace.db")

# Each required approver gets a HITL checkpoint in sequence:
for approver in config.required_for(highest_risk):
    reviewer = HitlReviewer(
        artifact_id=f"release_{release.id}",
        trace_log=tlog,
        run_id=run_id,
    )
    verdict = reviewer.review(release_summary)
    if verdict["verdict"] != "approve":
        release.reject()
        break
else:
    release.approve()
```

The TraceLog records every approval with `reviewer_id`, timestamp and verdict — forming the tamper-evident audit trail required by the [Compliance Pack](../platform/compliance-pack.md).

---

## End-to-end example

See the [Change-management pipeline guide](../guides/change-management.md) for a complete walkthrough from VCS diff to ServiceNow Change Request.
