# Adapters — VCS, Issue Tracker & ServiceNow

Adapters are the pluggable integration layer between antcrew and external systems. Business logic (`ImpactAnalyzer`, `ChangePackager`, release CLI) programs against neutral Protocol interfaces and never imports VCS or ITSM concepts directly. Concrete implementations are selected by config.

```
antcrew.adapters.vcs        → VCSAdapter  (SVN, Git)
antcrew.adapters.tracker    → IssueTrackerAdapter  (Jira, …)
antcrew.adapters.servicenow → ServiceNowClient  (Change Request API)
```

---

## VCS Adapter

### Protocol

```python
from antcrew.adapters.vcs import VCSAdapter, ChangeSet, FileDiff, get_vcs_adapter
```

**`ChangeSet`**

| Field | Type | Description |
|---|---|---|
| `changeset_id` | `str` | Revision number (SVN) or commit hash (Git) |
| `author` | `str` | Commit author |
| `message` | `str` | Full commit message |
| `timestamp` | `str` | ISO-8601 timestamp |
| `change_ref` | `str` | CR number extracted from the message (e.g. `CR-1234`) |
| `changed_files` | `list[str]` | File paths touched by this changeset |

**`FileDiff`**

| Field | Type | Description |
|---|---|---|
| `path` | `str` | File path |
| `diff_text` | `str` | Raw unified diff |
| `change_type` | `str` | `added` / `modified` / `deleted` / `renamed` |

**`VCSAdapter` methods**

| Method | Returns | Description |
|---|---|---|
| `changes_for_ref(change_ref)` | `list[ChangeSet]` | All changesets whose message contains `change_ref` |
| `diff(changeset_id)` | `list[FileDiff]` | Per-file diffs for one changeset |
| `changed_files(changeset_id)` | `list[str]` | File paths changed by one changeset |

### Implementations

#### SVN (`antcrew.adapters.vcs.svn.SVNAdapter`)

```yaml
# agentteam.yaml
vcs:
  type: svn
  url: https://svn.company.com/repos/main
  username: svcaccount
  password: "${SVN_PASSWORD}"
  cr_pattern: "\\b(CR-\\d+)\\b"        # regex to extract CR from commit message
  trunk_path: trunk/                   # relative to repo root
```

```python
from antcrew.adapters.vcs import get_vcs_adapter

vcs = get_vcs_adapter({"type": "svn", "url": "https://svn.company.com/repos/main"})
changesets = vcs.changes_for_ref("CR-1234")
for cs in changesets:
    print(cs.changeset_id, cs.changed_files)
```

#### Git (`antcrew.adapters.vcs.git.GitAdapter`)

```yaml
vcs:
  type: git
  repo_path: /opt/repos/myapp        # local clone path
  cr_pattern: "\\b(CR-\\d+)\\b"
  default_branch: main
```

```python
vcs = get_vcs_adapter({"type": "git", "repo_path": "/opt/repos/myapp"})
diffs = vcs.diff("a1b2c3d4")
```

### Custom adapters

Implement `VCSAdapter` (a `typing.Protocol`) for any other VCS:

```python
from antcrew.adapters.vcs import VCSAdapter, ChangeSet, FileDiff

class TFSAdapter:
    def changes_for_ref(self, change_ref: str) -> list[ChangeSet]: ...
    def diff(self, changeset_id: str) -> list[FileDiff]: ...
    def changed_files(self, changeset_id: str) -> list[str]: ...
```

---

## Issue Tracker Adapter

### Protocol

```python
from antcrew.adapters.tracker import IssueTrackerAdapter, Issue, IssueFilter, get_tracker_adapter
```

**`IssueFilter`**

| Field | Type | Description |
|---|---|---|
| `state` | `str \| None` | Logical state name, e.g. `"uat"` |
| `project` | `str \| None` | Project key / board |
| `change_refs` | `list[str]` | Filter issues that reference these CRs |
| `labels` | `list[str]` | Label filter |
| `assignee` | `str \| None` | Assignee filter |
| `max_results` | `int` | Default `200` |

**`Issue`**

| Field | Type | Description |
|---|---|---|
| `key` | `str` | Issue key, e.g. `PROJ-123` |
| `summary` | `str` | Issue title |
| `status` | `str` | Logical state |
| `change_ref` | `str` | CR number extracted from the issue |
| `assignee` / `reporter` | `str` | User identifiers |
| `extra` | `dict` | Raw provider-specific fields |

**`IssueTrackerAdapter` methods**

| Method | Returns | Description |
|---|---|---|
| `fetch_issues(filter)` | `list[Issue]` | Issues matching the filter |
| `get_issue(key)` | `Issue \| None` | One issue by key |
| `update_issue(key, fields)` | `None` | Update fields (neutral names, adapter maps to JQL) |

### Jira (`antcrew.adapters.tracker.jira.JiraAdapter`)

```yaml
tracker:
  type: jira
  url: https://company.atlassian.net
  token: "${JIRA_TOKEN}"          # PAT or API token
  project: MYPROJ
  state_map:                      # logical → Jira status names
    uat: "UAT"
    ready_for_prod: "Ready for Production"
    done: "Done"
```

```python
from antcrew.adapters.tracker import get_tracker_adapter, IssueFilter

tracker = get_tracker_adapter({
    "type": "jira",
    "url": "https://company.atlassian.net",
    "token": "...",
})
issues = tracker.fetch_issues(IssueFilter(state="uat", change_refs=["CR-1234"]))
```

---

## ServiceNow Adapter

Wraps the [ServiceNow Table API v2](https://developer.servicenow.com/dev.do#!/reference/api/tokyo/rest/c_TableAPI) for `change_request` records. Field names vary per ServiceNow instance; all mapping is via `field_map` in config.

### Models

```python
from antcrew.adapters.servicenow import ServiceNowClient, ChangeRecord, ChangeRecordInput
```

**`ChangeRecordInput`** — fields to set on create/update

| Field | Notes |
|---|---|
| `short_description` | Maps to `short_description` by default |
| `description` | Long-form description |
| `risk` | `low` / `moderate` / `high` (mapped via `risk_map`) |
| `state` | Logical state (mapped via `state_map`) |
| `change_ref` | Maps to `u_change_ref` by default — adjust to your instance |
| `risk_level` | Maps to `u_risk_level` by default |
| `assignment_group` / `requested_by` | Assignment |
| `start_date` / `end_date` | ISO-8601 strings |

**`ChangeRecord`** — result returned from the API (same fields plus `sys_id`, `number`, `url`).

### Configuration

```yaml
# agentteam.yaml
servicenow:
  instance_url: https://mycompany.service-now.com
  # Auth — token (Bearer) or user/password (Basic):
  token: "${SERVICENOW_TOKEN}"
  # user: svcaccount
  # password: "${SERVICENOW_PASSWORD}"

  # Override only the fields that differ in your instance:
  field_map:
    change_ref: u_cr_number        # custom field name in your instance
    risk_level: u_risk_classification

  risk_map:
    low: low
    medium: moderate
    high: high

  state_map:
    new: new
    in_progress: in progress
    approved: approved
    deployed: closed
```

### Python API

```python
from antcrew.adapters.servicenow import ServiceNowClient, ChangeRecordInput

client = ServiceNowClient(
    instance_url="https://mycompany.service-now.com",
    token="...",
)

# Create
record = client.create_change(ChangeRecordInput(
    short_description="Deploy CR-1234 — accounts module refactor",
    change_ref="CR-1234",
    risk="medium",
    state="new",
))
print(record.sys_id, record.number)   # SYS_ID, CHG0001234

# Update
client.update_change(record.sys_id, ChangeRecordInput(state="approved"))

# Look up by CR reference
existing = client.find_by_change_ref("CR-1234")
```

### CLI

```bash
# Create Change Request from release config (dry run first):
antcrew release submit --config agentteam.yaml --dry-run

# Create and persist sys_id back to releases.json:
antcrew release submit --config agentteam.yaml

# Update an existing CR:
antcrew release submit --config agentteam.yaml --update SYS_ID_HERE

# JSON output:
antcrew release submit --config agentteam.yaml --json
```

See the [Change-management pipeline guide](../guides/change-management.md) for an end-to-end walkthrough.

---

## Factory functions

All three adapters expose a `get_*_adapter(cfg: dict)` factory that reads the config dict and returns the correct implementation:

```python
from antcrew.adapters import get_vcs_adapter, get_tracker_adapter, get_servicenow_adapter

vcs     = get_vcs_adapter(config["vcs"])
tracker = get_tracker_adapter(config["tracker"])
snow    = get_servicenow_adapter(config["servicenow"])
```
