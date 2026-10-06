# Getting started

## 1. Create your account

Go to [antcrew.org/onboard](https://antcrew.org/onboard) and create an account. A 6-digit code is sent to your email — enter it to verify. Once verified you land on the dashboard.

The onboarding wizard guides you through:

- Choosing an LLM mode (Managed / BYOK / Proxy)
- Adding provider keys if using BYOK

## 2. Get your API key

Every workspace comes with an API key shown in **Settings → API Keys**. Use it in the `X-Api-Key` header for all requests.

## 3. Your first run

```bash
# Start a pipeline run
curl -X POST https://antcrew.org/run/ \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: acw_..." \
  -d '{"team": "DevTeam", "request": "Build a user authentication module"}'

# → {"run_id": "abc123", "status": "running"}
```

Or with the Python SDK:

```python
import httpx

client = httpx.Client(
    base_url="https://antcrew.org",
    headers={"X-Api-Key": "acw_..."},
)

r = client.post("/run/", json={
    "team": "DevTeam",
    "request": "Build a user authentication module",
})
run_id = r.json()["run_id"]
```

## 3b. Conversational discovery (alternative)

If the request isn't clear-cut, use the **Discover** tab before creating a run. The `DiscoveryAgent` asks 1–7 targeted questions, then a "Finalizar y crear run" button converts the gathered requirements into a pre-filled run.

You can also drive it via API:

```bash
# Start a discovery session
curl -X POST https://antcrew.org/discovery/sessions \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{"request": "I need something but am not sure of the scope"}'
# → {"session_id": "...", "question": "What is the main goal of this feature?", "round": 1}

# Answer each question — repeat until status="complete"
curl -X POST https://antcrew.org/discovery/sessions/{session_id}/answer \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{"answer": "A user authentication module with OAuth and email verification"}'
```

Discovery sessions expire after `DISCOVERY_SESSION_TTL_DAYS` days of inactivity (default: 7).

## 4. Stream events

```bash
# Poll status
curl https://antcrew.org/runs/abc123 -H "X-Api-Key: acw_..."

# Stream events (Server-Sent Events)
curl -N https://antcrew.org/runs/abc123/events -H "X-Api-Key: acw_..."

# WebSocket (all runs)
wscat -c wss://antcrew.org/ws/events -H "X-Api-Key: acw_..."
```

## 5. Configure models per agent (optional)

By default every agent in a run uses the workspace's LLM. You can assign different models to different agents — either as a workspace default or per run.

```bash
# Set workspace defaults: fast model for most agents, stronger for backend
curl -X PATCH https://antcrew.org/workspaces/{id}/agent-models \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{
    "agent_models": {
      "default": "groq:llama-3.3-70b-versatile",
      "BackendDevAgent": "claude-sonnet-5"
    }
  }'
```

Or override per run:

```bash
curl -X POST https://antcrew.org/run/ \
  -H "X-Api-Key: acw_..." \
  -d '{"team": "DevTeam", "request": "...", "model_overrides": {"BackendDevAgent": "deepseek:deepseek-chat"}}'
```

Save frequent configurations as **presets** in the dashboard (Runs → New Run → Configurar modelos por agente → Guardar preset). See [Model configuration](model-config.md) for the full reference.

## 6. Monitor in the dashboard

Open [antcrew.org](https://antcrew.org) in a browser. The **Runs** tab shows live status, agent event timelines, and generated tickets. The **Trace** tab in each run shows a per-agent execution timeline with model assignment, duration, and artifacts produced. The **Reviews** tab is where HITL decisions happen.

---

## Auth flow reference

| Step | Endpoint | Notes |
|---|---|---|
| Register | `POST /auth/register` | Creates user + workspace |
| Verify email | `POST /auth/verify-email` | 6-digit code sent on register |
| Resend code | `POST /auth/resend-code` | Re-sends verification code |
| Login | `POST /auth/login` | Returns session cookie; if MFA enabled, returns `mfa_token` instead |
| MFA challenge | `POST /auth/mfa/challenge` | `{mfa_token, code}` → sets session cookie |
| Session info | `GET /auth/me` | Returns `{email, role, email_verified, mfa_enabled}` |

Browser sessions use an `antcrew_session` cookie. API clients use `X-Api-Key`.

---

## LLM modes

| Mode | Who provides the key | Price multiplier | How to configure |
|---|---|---|---|
| **Managed** | antcrew provides the key | ×3.0 | Default — no configuration needed |
| **BYOK** | Your own provider key | ×0.4 | Settings → Providers → add key |
| **Proxy** | Your key, via `keybridge` | ×0.7 | Run `keybridge`; Settings → Providers → set proxy URL |

BYOK and Proxy keys are encrypted at rest and never stored in plaintext.

> **Trial accounts** start with a **$5.00 credit** in Managed mode. When the credit is exhausted the next run returns an error showing the amount spent. Upgrade to a paid plan from workspace settings to continue.
> The credit limit is configurable via the `TRIAL_CREDIT_USD` environment variable (platform admins only).
