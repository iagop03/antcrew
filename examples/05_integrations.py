"""
05 — External integrations: Jira + GitHub

After the pipeline runs, push the generated tickets to Jira and open a
GitHub PR with the code changes.

Requires:
    export JIRA_TOKEN=...
    export GH_TOKEN=...
    export ANTHROPIC_API_KEY=...   (or use SimulatedLLM)

Swap the placeholders below for your actual Jira URL, project key, and repo.
"""
import os

from antcrew import DevTeam
from antcrew.integrations import JiraIntegration, GitHubIntegration
from antcrew.models import SimulatedLLM  # swap for AnthropicModel() in production

# ── Run the pipeline ──────────────────────────────────────────────────────────
team  = DevTeam(model=SimulatedLLM())
state = team.run("Build a user notification system with email and push alerts")

print(f"Generated {len(state.get('tickets', []))} tickets and "
      f"{len(state.get('code_artifacts', []))} code files.\n")

# ── Push tickets to Jira ──────────────────────────────────────────────────────
jira_token = os.environ.get("JIRA_TOKEN")
if jira_token:
    jira = JiraIntegration(
        url="https://your-org.atlassian.net",
        email="you@your-org.com",
        api_token=jira_token,
        project_key="DEV",
    )
    pairs = jira.sync_tickets(state.get("tickets", []))
    for ticket, issue_key in pairs:
        print(f"  Jira: {issue_key} — {ticket.title}")
else:
    print("JIRA_TOKEN not set — skipping Jira sync.")

# ── Open a GitHub PR ──────────────────────────────────────────────────────────
gh_token = os.environ.get("GH_TOKEN")
if gh_token:
    gh = GitHubIntegration(
        token=gh_token,
        repo="your-org/your-repo",
    )
    pr_url = gh.create_pr(state)
    print(f"\nPR opened: {pr_url}")
else:
    print("GH_TOKEN not set — skipping GitHub PR.")
