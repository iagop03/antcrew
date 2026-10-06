# GitHub App integration

The antcrew GitHub App connects your repositories to your workspace. Once installed it enables two things:

- **Write-back** — runs and discovery sessions can open a GitHub PR directly from the dashboard
- **Push triggers** — a push to any branch can automatically dispatch a DevTeam run

## Installing the app

1. Go to **Settings → GitHub** in the dashboard.
2. Click **Conectar GitHub App**. GitHub opens an authorization page.
3. Select the organization or user account and choose which repositories the app can access.
4. GitHub redirects back and the installation appears in the GitHub card.

One workspace can have multiple installations (e.g. a personal account and an organization). One installation cannot be shared across workspaces — each workspace installs independently.

## Repo allowlist

By default an installation gives the app access to all repos you selected on GitHub. You can further restrict which repos antcrew uses by adding them to the allowlist in **Settings → GitHub → Repos**. Only the listed repos will be available for write-back and push triggers.

## Write-back (PRs from runs)

After a run completes you can open a GitHub PR directly from the run detail page:

1. Open the run, go to **Artifacts**.
2. Click **Publicar en GitHub**.
3. Select the repository and branch prefix.

The platform creates a branch (`antcrew-engine/<run-id>`), commits all code artifacts, and opens a PR with a structured summary that includes which capabilities ran, what conditions were satisfied, and a link back to the run.

For engine runs triggered via API, use `POST /engine/runs/{run_id}/publish`:

```bash
curl -X POST https://antcrew.org/engine/runs/run_abc123/publish \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{"repo": "my-org/my-repo", "branch_prefix": "antcrew/"}'
```

## Push triggers

A push trigger dispatches a DevTeam run automatically when you push to a branch. Useful for continuous improvement loops: every push to `main` gets an automatic code review, test gap analysis, or documentation update.

### Configuring a push trigger

In **Settings → GitHub**, open the installation card and fill in the **Push trigger** section:

| Field | Description |
|---|---|
| **Goal** | The task the DevTeam run will execute. Leave empty to disable the trigger. |
| **Model** | Model string for the run (e.g. `claude`, `groq:llama-3.1-70b-versatile`). Default: `claude`. |
| **Branch filter** | Which branch triggers a run. Use `*` for all branches, or a branch name like `main`. Default: `*`. |

Click **Guardar trigger** to save. The trigger is active immediately.

### Via API

```bash
curl -X PATCH https://antcrew.org/github/installations/{installation_id}/push-trigger \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{
    "push_goal": "Review the diff for bugs and suggest fixes as inline comments",
    "push_model": "claude:claude-sonnet-5",
    "push_branch_filter": "main"
  }'
```

Pass `push_goal: null` to disable the trigger without deleting the installation.

### How push routing works

1. GitHub sends a `push` event to `POST /webhooks/github` (registered in your GitHub App settings).
2. antcrew looks up all installations matching the `installation_id` in the event.
3. For each installation it checks: does `push_goal` exist? Does the pushed `ref` match the branch filter? Is the repo in the allowlist?
4. Matching installations each get a fire-and-forget DevTeam run dispatched in the workspace linked to that installation.
5. The webhook returns `200` to GitHub immediately — dispatch happens asynchronously.

### Branch filter matching

The filter is matched against both the full Git ref (`refs/heads/main`) and the short branch name (`main`). Either form works when configuring the filter.

| Filter value | Matches |
|---|---|
| `*` | All branches |
| `main` | Only pushes to `main` |
| `refs/heads/release` | Only pushes to `release` |

## Disconnecting

Click **Desconectar** on the installation card, or call `DELETE /github/installations/{installation_id}`. This removes the antcrew record only — the app itself remains installed on GitHub. To fully uninstall, remove it from your GitHub organization's installed apps page.
