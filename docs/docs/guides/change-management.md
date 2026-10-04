# Change-management pipeline

antcrew's change-management pipeline links every AI run to a change-request, analyses
COBOL impact, routes changes for approval, and produces artefacts (Excel, email, Markdown)
ready for ServiceNow or any ITSM tool.

## Prerequisites

```bash
pip install --upgrade antcrew
pip install "antcrew[release]"   # adds openpyxl for Excel export
```

---

## Step 1 — Tag runs with a change-request number

Every run can carry a `change_ref`. The tag persists in TraceLog and (when using the platform) in the `run` table.

```bash
antcrew run "Implement order validation" --change-ref CR-4201
```

Via environment variable (for CI pipelines):

```bash
export ANTCREW_CHANGE_REF=CR-4201
antcrew run "..."
```

Via `agentteam.yaml`:

```yaml
change_ref: CR-4201
```

Filter the trace by CR:

```bash
antcrew trace --change-ref CR-4201
```

---

## Step 2 — Connect your VCS

Configure your VCS adapter in `agentteam.yaml`. The adapter is used by the COBOL index and by `release create --from-jira-state`.

**SVN:**

```yaml
vcs:
  type: svn
  url: svn://svn.company.com/repos/main
  cr_pattern: "CR-\\d+"   # regex to extract CR numbers from commit messages
```

Set `SVN_USERNAME` and `SVN_PASSWORD` environment variables for authentication.

**Git:**

```yaml
vcs:
  type: git
  repo_path: /path/to/repo        # defaults to cwd
  cr_pattern: "CR-\\d+"
```

---

## Step 3 — Connect your issue tracker

```yaml
tracker:
  type: jira
  base_url: https://company.atlassian.net
  state_map:
    open: ["Open", "To Do", "In Progress"]
    uat:  ["UAT", "User Acceptance Testing"]
    done: ["Done", "Closed", "Resolved"]
```

Set `JIRA_USER` and `JIRA_TOKEN` environment variables.

---

## Step 4 — Build the COBOL inverse index

If your codebase includes COBOL, build an index to enable impact analysis:

```python
from antcrew.augment.cobol.index import COBOLIndex

idx = COBOLIndex(db_path="cobol_index.db")
idx.build_from_directory("src/cobol")
print(idx.stats())
# → {"programs": 47, "calls": 163, "copies": 89, "db2_tables": 12}
```

Update incrementally after each commit:

```python
changed = adapter.changed_files(changeset_id)   # from VCSAdapter
idx.update_from_files([f.path for f in changed])
```

---

## Step 5 — Analyse impact

```python
from antcrew.augment.impact.analyzer import ImpactAnalyzer

analyzer = ImpactAnalyzer(idx)
result = analyzer.analyze(
    changed_files=["ACCTUPD.cbl", "ACCTREC.cpy"],
    change_ref="CR-4201",
)
print(result.risk_level)     # "high" / "medium" / "low"
print(result.as_markdown())  # paste into a change ticket
```

Risk levels:

| Risk | When |
|---|---|
| `high` | DB2 writes (INSERT/UPDATE/DELETE) or >5 downstream callers |
| `medium` | DB2 reads or 2-5 downstream callers |
| `low` | No DB2 access and ≤1 downstream caller |

---

## Step 6 — Create a release

Group one or more CRs into a named release:

```bash
# Explicit refs
antcrew release create Sprint-42 --ref CR-4201 --ref CR-4202 \
  --target-date 2026-11-01

# Pull from Jira (all issues currently in UAT state)
antcrew release create Sprint-42 --from-jira-state uat
```

```bash
antcrew release list
antcrew release show REL-A1B2C3D4
```

---

## Step 7 — Configure approvers

Create `approvers.yaml`:

```yaml
approvers:
  - role: qa_lead
    name: "María García"
    email: mgarcia@company.com
    required_if:
      risk: [medium, high]

  - role: risk_officer
    name: "John Smith"
    email: jsmith@company.com
    required: always        # always required regardless of risk

  - role: business_owner
    name: "Ana Pérez"
    email: aperez@company.com
    required_if:
      risk: [high]

# Optional: customize Excel columns for your ServiceNow template
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

---

## Step 8 — Build the change package

```bash
antcrew release package REL-A1B2C3D4 \
  --output-dir ./packages \
  --approvers approvers.yaml \
  --trace ~/.antcrew/trace.db
```

This produces:

| File | Description |
|---|---|
| `REL-A1B2C3D4_changes.xlsx` | One row per CR; risk-colored rows; ServiceNow-ready |
| `REL-A1B2C3D4_summary.md` | Human-readable release summary |
| `REL-A1B2C3D4_email_drafts.json` | Pre-written approval request per approver |

Excel requires `pip install "antcrew[release]"`. The other two artefacts are always produced.

---

## Step 9 — Record approvals

```bash
antcrew release approve REL-A1B2C3D4 \
  --decision approved \
  --approver-id mgarcia@company.com \
  --role qa_lead \
  --reason "Tested in UAT, all scenarios pass" \
  --trace ~/.antcrew/trace.db
```

Passing `--trace` writes the approval into the tamper-evident SHA-256 hash chain alongside HITL decisions. Verify the chain at any time:

```bash
antcrew trace --verify-chain ~/.antcrew/trace.db
```

State machine: `draft → pending_approval → approved / rejected → deployed`

---

## Python API

All of the above is also available programmatically:

```python
from antcrew.adapters.vcs import get_vcs_adapter
from antcrew.adapters.tracker import get_tracker_adapter, IssueFilter
from antcrew.augment.cobol.index import COBOLIndex
from antcrew.augment.impact.analyzer import ImpactAnalyzer
from antcrew.models.release import Release
from antcrew.packager import ChangePackager
from antcrew.packager.approvers import ApproversConfig

# VCS
adapter = get_vcs_adapter({"type": "svn", "url": "svn://...", "cr_pattern": "CR-\\d+"})
changesets = adapter.changes_for_ref("CR-4201")

# COBOL index
idx = COBOLIndex()
idx.build_from_directory("src/cobol")

# Impact
analysis = ImpactAnalyzer(idx).analyze(
    changed_files=[f.path for cs in changesets for f in adapter.changed_files(cs.changeset_id)],
    change_ref="CR-4201",
)

# Release
release = Release(id="REL-001", name="Sprint-42")
release.add_item("CR-4201", summary=analysis.as_markdown(), impact_risk=analysis.risk_level)
release.submit_for_approval()

# Package
config = ApproversConfig.from_yaml("approvers.yaml")
packager = ChangePackager(release, approvers_config=config)
result = packager.build(output_dir="./packages")
print(result.excel_path)
print(result.email_drafts)
```

---

## Step 10 — Submit to ServiceNow

Once a release is approved, create a Change Request in ServiceNow directly from the CLI.

Configure your instance in `agentteam.yaml`:

```yaml
servicenow:
  instance_url: https://mycompany.service-now.com
  # Auth: SERVICENOW_TOKEN env var (Bearer) or SERVICENOW_USER + SERVICENOW_PASSWORD (Basic)

  # Field mapping — adjust to your instance's custom field names
  field_map:
    change_ref:  u_change_ref     # custom field on your instance
    risk_level:  u_risk_level     # custom field on your instance

  # Map neutral risk levels → ServiceNow risk values
  risk_map:
    low:    low
    medium: moderate
    high:   high

  # Map release states → ServiceNow state values
  state_map:
    approved: approved
    deployed: closed
```

Create the Change Request:

```bash
antcrew release submit REL-A1B2C3D4
# → CHG0012345  https://mycompany.service-now.com/nav_to.do?uri=change_request.do?sys_id=...
```

Preview the payload without sending:

```bash
antcrew release submit REL-A1B2C3D4 --dry-run
```

Update an existing Change Request:

```bash
antcrew release submit REL-A1B2C3D4 --update <sys_id>
```

The returned `sys_id`, `number`, and URL are persisted back to `~/.antcrew/releases.json` so they appear in `antcrew release show`.

### Python API

```python
from antcrew.adapters.servicenow import get_servicenow_adapter, ChangeRecordInput

adapter = get_servicenow_adapter({
    "instance_url": "https://mycompany.service-now.com",
    "field_map": {"change_ref": "u_change_ref", "risk_level": "u_risk_level"},
})

record = adapter.create_change(ChangeRecordInput(
    short_description="Deploy Sprint-42 — CR-4201, CR-4202",
    change_ref="CR-4201, CR-4202",
    risk_level="medium",
    state="new",
))
print(record.number, record.url)

# Update later
adapter.update_change(record.sys_id, ChangeRecordInput(state="approved"))
```
