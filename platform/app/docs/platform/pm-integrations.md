# PM integrations — GitHub Issues, Linear, Jira

AntCrew can push every ticket a pipeline produces directly to your issue tracker. No copy-paste: when a run finishes, tickets appear automatically in GitHub Issues, Linear, or Jira.

## How it works

1. You configure one or more **destinations** (`POST /integrations/`) with provider credentials.
2. When any run in your workspace completes, AntCrew reads the tickets it produced and calls the provider API for each one.
3. `manual_action` tickets (human-only work items) are skipped — only automatable tickets are synced.

You can have multiple destinations at once — for example, GitHub Issues for your main repo and Linear for your product backlog.

## Supported providers

| Provider | What gets created | Auth |
|---|---|---|
| `github` | GitHub Issue in a specific repo | Personal access token (repo scope) |
| `linear` | Linear Issue in a specific team | Linear API key + team ID |
| `jira` | Jira Task in a project | Jira cloud URL + email + API token + project key |

## Configure a destination

```bash
POST /integrations/
Content-Type: application/json
X-Api-Key: acw_...

{
  "provider": "github",
  "label": "Main backend repo",
  "config_json": {
    "token": "ghp_...",
    "owner": "myorg",
    "repo": "backend"
  }
}
```

### GitHub config

```json
{
  "token": "ghp_...",
  "owner": "myorg",
  "repo": "backend"
}
```

Requires a [personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) with `repo` scope (or `public_repo` for public repos).

### Linear config

```json
{
  "api_key": "lin_api_...",
  "team_id": "TEAM-UUID"
}
```

Generate a [Linear API key](https://linear.app/settings/api) in workspace settings. The team ID is the UUID from your Linear team URL.

### Jira config

```json
{
  "base_url": "https://mycompany.atlassian.net",
  "email": "you@company.com",
  "api_token": "ATATT...",
  "project_key": "BACK"
}
```

Generate a [Jira API token](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) in your Atlassian account settings.

## Filter by team

Pass `team_filter` to restrict a destination to a specific team:

```json
{
  "provider": "linear",
  "label": "DevTeam → Linear",
  "team_filter": "DevTeam",
  "config_json": { ... }
}
```

Omit `team_filter` (or set it to `null`) to sync from all teams.

## Test the connection

```bash
POST /integrations/{dest_id}/test
```

Sends a harmless test ticket to the provider and returns `{"success": true, "url": "..."}`. Use this to verify credentials before relying on it in production.

## Manage destinations

```bash
# List all destinations (credentials are masked in the response)
GET /integrations/

# Update label, toggle enabled, change team_filter
PATCH /integrations/{dest_id}
{ "enabled": false }

# Remove a destination
DELETE /integrations/{dest_id}
```

## What gets synced

Each ticket becomes one issue in the provider. AntCrew maps the fields as follows:

| AntCrew field | GitHub | Linear | Jira |
|---|---|---|---|
| `title` | title | title | summary |
| `description` + `acceptance_criteria` | body | description | description |
| `priority` | label (not set) | priority (1–4) | not set |

Tickets with `ticket_type = "manual_action"` are never synced.

## Credential security

Credentials stored in `config_json` are kept in your workspace and never logged. The list endpoints return a masked view (first 4 chars + `***`) — the raw values are only read at sync time.
