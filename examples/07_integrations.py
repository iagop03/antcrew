"""
Integrations — Jira, GitHub, Confluence.

Pushes tickets to Jira, opens a PR on GitHub, and publishes the PRD
and generated docs to Confluence.

Required env vars:
    JIRA_URL, JIRA_EMAIL, JIRA_TOKEN, JIRA_PROJECT
    GITHUB_TOKEN, GITHUB_REPO   (e.g. "myorg/myapp")
    CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_TOKEN

Run:
    python examples/07_integrations.py
"""
import os

from antcrew import DevTeam
from antcrew.integrations import ConfluenceIntegration, GitHubIntegration, JiraIntegration
from antcrew.models import AnthropicModel

team = DevTeam(model=AnthropicModel())
state = team.run("Add an audit log for all write operations in the admin panel")

# ── Jira ─────────────────────────────────────────────────────────────────────
jira = JiraIntegration(
    url=os.environ["JIRA_URL"],
    email=os.environ["JIRA_EMAIL"],
    api_token=os.environ["JIRA_TOKEN"],
    project_key=os.environ.get("JIRA_PROJECT", "DEV"),
)
pairs = jira.sync_tickets(state.get("tickets") or [])
print("\nJira tickets created:")
for ticket, key in pairs:
    print(f"  {ticket.id} → {key}  ({ticket.title})")

# ── GitHub ────────────────────────────────────────────────────────────────────
gh = GitHubIntegration(
    token=os.environ["GITHUB_TOKEN"],
    repo=os.environ["GITHUB_REPO"],
)
pr_url = gh.create_pr(state)
print(f"\nGitHub PR: {pr_url}")

# ── Confluence ────────────────────────────────────────────────────────────────
confluence = ConfluenceIntegration(
    url=os.environ["CONFLUENCE_URL"],
    email=os.environ["CONFLUENCE_EMAIL"],
    api_token=os.environ["CONFLUENCE_TOKEN"],
)
space = os.environ.get("CONFLUENCE_SPACE", "ENG")

prd_page = confluence.publish_prd(state, space_key=space)
if prd_page:
    print(f"\nConfluence PRD: {prd_page.get('_links', {}).get('webui', '(created)')}")
