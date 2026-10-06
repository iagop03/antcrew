# CLI & API integration

This page explains how to connect the `antcrew` CLI and SDK to a running antcrew-platform instance.

---

## Prerequisites

- `antcrew` CLI installed: `pip install antcrew`
- A running antcrew-platform instance (self-hosted or managed)
- An API key with `write` role (create one in **Settings → API Keys**)

---

## 1. Configure your environment

Export two environment variables — the platform URL and your API key:

```bash
export ANTCREW_PLATFORM_URL="https://platform-int.antcrew.org"
export ANTCREW_API_KEY="acw_live_..."
```

Add them to your `.env` file or shell profile so they persist across sessions.

!!! tip "Where to find these"
    - **Platform URL**: the URL you use to open the dashboard (e.g. `https://platform-int.antcrew.org`)
    - **API key**: Settings → API Keys → create a key with role `write` or `admin`

---

## 2. Launch a run from the CLI

```bash
antcrew run "Build a REST API for user authentication" \
  --team dev \
  --platform-url "$ANTCREW_PLATFORM_URL" \
  --api-key "$ANTCREW_API_KEY"
```

Or use environment variables so you don't need to pass them every time:

```bash
antcrew run "Refactor the auth module" --team dev
```

The CLI picks up `ANTCREW_PLATFORM_URL` and `ANTCREW_API_KEY` automatically.

!!! note "CLI syntax"
    The run request is a **positional argument**, not a flag. Valid `--team` values are lowercase:
    `dev`, `fullstack`, `minimal`, `research`, `content`, `custom`, `feature`, `auto`, `routed`, `direct`.

---

## 3. Run from Python

```python
import httpx

BASE = "https://platform-int.antcrew.org"
KEY  = "acw_live_..."

with httpx.Client(base_url=BASE, headers={"X-Api-Key": KEY}) as client:
    # Start a run
    r = client.post("/run/", json={
        "team": "DevTeam",
        "request": "Build a user authentication module",
    })
    run_id = r.json()["run_id"]
    print(f"Run started: {run_id}")

    # Poll until done
    import time
    while True:
        status = client.get(f"/runs/{run_id}").json()["status"]
        print(f"Status: {status}")
        if status in ("success", "error", "cancelled"):
            break
        time.sleep(3)

    # Get artifacts
    artifacts = client.get(f"/runs/{run_id}/artifacts").json()
    print(artifacts)
```

---

## 4. Stream events in real time

```python
import httpx

with httpx.stream("GET", f"{BASE}/runs/{run_id}/events",
                  headers={"X-Api-Key": KEY}) as stream:
    for line in stream.iter_lines():
        if line.startswith("data:"):
            print(line[5:].strip())
```

Or via WebSocket (all runs):

```bash
wscat -c wss://platform-int.antcrew.org/ws/events \
  -H "X-Api-Key: acw_live_..."
```

---

## 5. Engine runs (goal-directed)

```bash
antcrew engine run \
  --goal "Audit all Python files for security vulnerabilities" \
  --platform-url "$ANTCREW_PLATFORM_URL" \
  --api-key "$ANTCREW_API_KEY"
```

From Python:

```python
r = client.post("/engine/run", json={
    "goal": "Audit all Python files for security vulnerabilities",
    "max_cost_usd": 5.0,
})
run_id = r.json()["run_id"]
```

---

## API key roles

| Role | Can do |
|---|---|
| `admin` | Everything — create workspaces, manage keys, run pipelines |
| `write` | Create and cancel runs, submit reviews |
| `reviewer` | Resolve HITL reviews only |
| `read` | Read runs and artifacts, no mutations |

For CI/CD pipelines, use a `write` key. For human reviewers who only need to approve HITL, use `reviewer`.

---

## CI/CD example (GitHub Actions)

```yaml
- name: Run antcrew pipeline
  env:
    ANTCREW_PLATFORM_URL: ${{ secrets.ANTCREW_PLATFORM_URL }}
    ANTCREW_API_KEY: ${{ secrets.ANTCREW_API_KEY }}
  run: |
    antcrew run "Review and test the changes in this PR" \
      --team dev \
      --wait          # block until the run completes
```

Store `ANTCREW_PLATFORM_URL` and `ANTCREW_API_KEY` as GitHub repository secrets.
