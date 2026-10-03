# Security

## Authentication model

antcrew-platform supports two authentication paths:

| Path | Used by | Credential |
|---|---|---|
| **Session cookie** | Browser users (dashboard, onboarding) | `antcrew_session` cookie set on login |
| **API key** | CLI, SDK, CI pipelines | `X-Api-Key` header or `PLATFORM_API_KEY` env var |

The backend (`get_workspace_context`) tries session first, then API key header, then the platform master key.

### Email + password registration

1. `POST /auth/register` — creates user + workspace, sends a 6-digit verification code by email
2. `POST /auth/verify-email` — activates the account
3. `POST /auth/login` — sets `antcrew_session` cookie; if MFA is enabled, returns a short-lived `mfa_token` instead
4. `POST /auth/mfa/challenge` — exchanges `mfa_token + TOTP code` for a full session cookie

## Multi-factor authentication (TOTP)

Users can enable TOTP-based MFA from **Settings → Profile** or the post-onboarding setup prompt. MFA is per-user and optional; workspace admins cannot currently enforce it for all members.

Setup flow:
1. `GET /auth/mfa/setup` — returns a TOTP secret and `otpauth://` provisioning URI (scan with Authenticator app)
2. `POST /auth/mfa/enable` — submits the secret and a verified 6-digit code to activate; requires CSRF token

Login flow when MFA is active:
1. `POST /auth/login` — returns `{mfa_required: true, mfa_token: "..."}` instead of setting a session cookie
2. `POST /auth/mfa/challenge` — submits `{mfa_token, code}`; sets the session cookie on success

MFA can be disabled at any time via `POST /auth/mfa/disable` (requires CSRF token and active session).

## API key model

- Each workspace has one or more **workspace API keys** with a role (`admin | write | read | reviewer`)
- A single key can be a member of multiple workspaces
- Keys can be rotated at any time without downtime
- The platform master key (`PLATFORM_API_KEY`) is a static env var that bypasses the DB check — useful for infrastructure scripts; **must be ≥ 32 characters** (startup blocks if shorter). Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## LLM key storage (BYOK)

Per-workspace LLM provider keys are encrypted at rest using Fernet (AES-128-CBC) under `BYOK_ENCRYPTION_KEY`. The encryption key is an environment variable on the server — Hetzner for PROD, Fly.io for INT — it is never transmitted over the network.

A single `BYOK_ENCRYPTION_KEY` protects all workspaces on the same instance. For workspaces requiring HSM-grade isolation, self-host with a KMS integration.

## CSRF protection

All state-changing browser endpoints (enable/disable MFA, workspace settings, etc.) require a CSRF token:
- Cookie: `csrf_token`
- Header: `X-CSRF-Token`

API key requests skip CSRF checks.

## HITL as a security gate

Human-in-the-loop reviews prevent unreviewed AI outputs from reaching production systems. Any run can be configured to require approval before its output is acted on. Public review links (`/reviews/token/{token}`) are single-use and scoped to a specific review.

## Audit log

Every review decision (approve/reject), every run state change, and every settings modification is written to an append-only audit log with actor, timestamp, and diff.

## Admin bootstrap

Platform admin access (`is_platform_admin = true`) is granted via:

```bash
curl -X POST https://your-platform.example.com/admin/make-admin \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PLATFORM_ADMIN_TOKEN" \
  -d '{"email": "you@example.com"}'
```

`PLATFORM_ADMIN_TOKEN` is an environment variable you set at deploy time. It is only needed once per environment (or whenever you need to grant admin to a new user). Platform admins can access `/admin` in the dashboard.

## Open mode

When neither `PLATFORM_API_KEY` nor any DB API keys are configured, the platform starts in **open (unauthenticated) mode** — all endpoints accessible without credentials. This is acceptable for local dev only.

On a **public host** (`HOST` not in `127.0.0.1`, `localhost`, `::1`), open mode is a hard startup error unless `ANTCREW_OPEN_MODE=true` is explicitly set. Before deploying to any public interface, configure authentication:

- **Option A** — set `PLATFORM_API_KEY` (≥ 32 chars)
- **Option B** — create DB-scoped keys via `POST /api-keys/`

To enforce auth even on localhost (useful in CI environments with DB access): `ANTCREW_REQUIRE_AUTH=true`.

## Content Security Policy (CSP)

The platform injects a cryptographic nonce into every HTML response via `_SecurityHeadersMiddleware`. Each request generates a fresh `secrets.token_urlsafe(16)` nonce that is:

1. Added to the `script-src` CSP directive as `'nonce-{value}'`
2. Injected as a `nonce="..."` attribute into every inline `<script>` tag in the response body

The effective `script-src` directive is:
```
script-src 'self' 'nonce-{random}' https://cdn.jsdelivr.net
```

This eliminates `'unsafe-inline'` from `script-src` — only scripts bearing the matching nonce can execute, blocking XSS payload injection. External scripts (Alpine.js from CDN) are controlled by the origin allowlist and do not require a nonce.

## SSRF and DNS rebinding

Outbound HTTP requests (webhooks, repository clones) pass through `validate_external_url()` which:

1. Validates the URL scheme (https required by default)
2. Blocks known internal hostnames (`localhost`, `169.254.169.254`, etc.)
3. Validates IP literals against private/reserved ranges
4. **Resolves domain names via DNS** (`socket.getaddrinfo`) and validates all returned IPs — this narrows the DNS-rebinding window between URL validation and the actual request

Pass `resolve_dns=False` only in tests or when an egress firewall provides the authoritative control. Network-level egress filtering (blocking outbound traffic to RFC 1918 ranges) remains the defense-in-depth control.

## Run state encryption

The `state` column in the `run` table (LLM prompts, generated code, intermediate artifacts) is encrypted with AES-GCM-256 when `ANTCREW_ENCRYPTION_KEY` is set. **Without it, run state is stored in plaintext.** In `APP_ENV=prod`, the platform refuses to start without this key.

Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## CI/CD secrets reference

| Secret | Scope | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM inference | Runtime — required for Managed mode |
| `BYOK_ENCRYPTION_KEY` | Per-workspace key encryption | Runtime — required for BYOK |
| `PLATFORM_ADMIN_TOKEN` | Admin bootstrap | Runtime — set once |
| `PLATFORM_API_KEY` | Master API key (optional, ≥32 chars) | Runtime |
| `ANTCREW_ENCRYPTION_KEY` | Run state AES-GCM-256 encryption | Runtime — required in production |
| `SEMGREP_APP_TOKEN` | SAST scanning in CI | CI (`ci.yml` security job) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | Email sending | Runtime — required for email |
| `HCLOUD_TOKEN` | Hetzner API | `deploy.yml` — create/delete UAT server |
| `HETZNER_SSH_PRIVATE_KEY` | SSH into UAT and PROD servers | `deploy.yml` |
| `PROD_SERVER_IP` | Hetzner PROD server IP | `deploy.yml` — PROD deploy target |
| `DATABASE_URL` | PostgreSQL connection | UAT and PROD runtime |
| `FLY_API_TOKEN` | Fly.io deploy | `deploy.yml` — INT deploy (auto on push to main) |
| `CLOUDFLARE_TOKEN` | DNS API | `deploy.yml` — update platform-uat DNS |

See the full [configuration reference](../platform/configuration.md) for all variables.
