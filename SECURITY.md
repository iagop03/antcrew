# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities to **security@antcrew.org**. Do not open public GitHub
issues for security bugs.

We aim to respond within **72 hours** and will coordinate a fix and disclosure timeline with you.

## Scope

This policy covers the `antcrew` Python SDK, including the bundled `antcrew_engine` package.

---

## Cryptographic Choices

### API keys passed to LLMs

API keys (ANTHROPIC_API_KEY, etc.) are read from environment variables and passed directly to
provider SDK clients. They are never logged, stored, or included in TraceLog entries. If you
use `FileLLMCache`, the cache key is a hash of the request content — no credentials are stored.

### TraceLog

TraceLog writes agent events to a SQLite file. It records prompts, responses, and token counts
but never records raw API keys or secrets. Entries are written append-only; the SQLite file
should be protected at the filesystem level (not world-readable).

---

## PR Checklist

Before merging a PR that touches any of the following surfaces, verify each item.

### 1. New filesystem write from untrusted input

> Pattern: `_safe_path(rel)` using `Path.is_relative_to()`, never `str.startswith()`.

- [ ] Is the path validated with `is_relative_to(root)` before any write?
- [ ] Is an absolute path from untrusted input explicitly rejected?
- [ ] Does the check run before both `mkdir` and `write_text`?

### 2. New code execution surface (tool or agent capability)

> Pattern: validate inputs; never pass untrusted strings to `shell=True`.

- [ ] Is `shell=False` (the default) used in all `subprocess.run()` / `Popen` calls?
- [ ] Is the subprocess environment stripped to a safe allowlist — no API keys, no DB URLs?
- [ ] If the tool accepts a file path from the LLM output, is it checked with `_safe_path()`?

### 3. New LLM model or provider

- [ ] Does `build_llm()` pass `api_key` only from trusted sources (env vars or explicit caller)?
- [ ] Is `extra_body` validated to contain only non-sensitive forwarded metadata?

### 4. New constant-time comparison

> Pattern: `hmac.compare_digest(a, b)` — never `==` on secret values.

- [ ] Are all comparisons involving tokens or keys using `hmac.compare_digest()`?
