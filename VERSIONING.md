# Versioning Policy

antcrew follows [Semantic Versioning](https://semver.org) with a pre-1.0 caveat:
while the major version is 0, minor bumps (0.35 → 0.36) may include breaking changes.
Patch bumps (0.35.0 → 0.35.1) are always backwards-compatible.

---

## Current stable: v0.35.x

### Guarantees

- Python 3.11, 3.12, 3.13, 3.14
- LangGraph >= 0.2
- Pydantic >= 2.0
- Anthropic SDK >= 0.30
- All public team classes (`FullStackTeam`, `DevTeam`, `ResearchTeam`, `ContentTeam`) are stable
- `RunResult` schema is stable (fields only added, never removed)
- `Ticket`, `PRD`, `ArtifactContract` schemas are stable
- CLI commands (`antcrew run`, `antcrew sprint`, `antcrew eval`, `antcrew replay`) are stable

### What may change in v0.36

- Internal agent constructors (not public API)
- Default model selection logic when no model is specified
- Team composition (agents added or reordered within a team)

---

## Pinning recommendation

**antcrew-platform** and other applications that depend on antcrew should pin the minor:

```toml
antcrew>=0.35.0,<0.36.0
```

This prevents surprise breaking changes while allowing patch updates.

---

## Changelog cadence

Breaking changes are announced in the release notes with a `BREAKING:` prefix.
Deprecations are announced at least one minor version before removal.

---

## Tested LLM providers

| Provider | Tested versions |
|---|---|
| Anthropic Claude | claude-sonnet-4-6, claude-opus-5, claude-haiku-4-5 |
| OpenAI | gpt-4o, gpt-4o-mini |
| Groq | llama-3.3-70b-versatile |
| Gemini | gemini-2.0-flash |
| Local (via remote-gateway) | claude-code, ollama, lmstudio, vllm |
