# Multi-LLM cost routing automático por agente

El cost routing automático selecciona el modelo LLM óptimo para cada agente sin configuración manual. En lugar de fijar `agent_models` agente por agente, defines una política a nivel de workspace y la plataforma hace el resto.

## Políticas disponibles

| Política | Comportamiento |
|----------|---------------|
| `none` | Sin routing automático (por defecto). Se usan los modelos de `agent_models` y los overrides de run-level. |
| `economy` | Todos los agentes usan el modelo barato de la plataforma. Máximo ahorro de coste. |
| `auto` | Cada agente se clasifica por nombre y se le asigna el tier apropiado (cheap / standard / premium). |

## Precedencia de modelos

El routing tiene la **menor prioridad**. Las configuraciones explícitas siempre ganan:

1. `model_overrides` de run-level (explícito, por llamada)
2. Entradas nombradas en `workspace.agent_models` (explícito, por workspace)
3. **Cost routing** — este módulo (economy o auto)
4. `workspace.agent_models["default"]`
5. `PlatformConfig.default_agent_models["default"]`
6. `"claude"` (hardcoded fallback)

## Clasificación de agentes (política `auto`)

Los agentes se clasifican por palabras clave en su nombre de clase:

**Cheap** — tareas simples y deterministas:
`validator`, `formatter`, `extractor`, `summarizer`, `summary`, `router`, `sink`, `monitor`

**Premium** — razonamiento complejo:
`architect`, `planner`, `legal`, `compliance`, `security`, `auditor`

**Standard** — todo lo demás:
`developer`, `dev`, `coder`, `reviewer`, `researcher`, `writer`, `tester`, `engineer`

Los equipos también se clasifican: `LegalReviewTeam` y `SecurityTeam` son premium; `WebhookSink` es cheap; el resto son standard.

## Modelos de tier por defecto

Configurables desde el panel de admin o via `PATCH /admin/billing-rates`:

| Tier | Modelo por defecto |
|------|-------------------|
| cheap | `groq:llama-3.3-70b-versatile` |
| standard | `claude:claude-sonnet-5` |
| premium | `claude:claude-opus-5` |

## API

### Leer la política de un workspace

```
GET /workspaces/{id}/routing
Authorization: Bearer <admin-key>
```

Respuesta:
```json
{
  "workspace_id": 42,
  "cost_routing_policy": "auto",
  "tier_cheap_model": "groq:llama-3.3-70b-versatile",
  "tier_standard_model": "claude:claude-sonnet-5",
  "tier_premium_model": "claude:claude-opus-5"
}
```

### Cambiar la política

```
PATCH /workspaces/{id}/routing
Authorization: Bearer <admin-key>
Content-Type: application/json

{"cost_routing_policy": "economy"}
```

Valores válidos: `"none"`, `"economy"`, `"auto"`.

### Configurar modelos de tier (admin global)

```
PATCH /admin/billing-rates
Authorization: Bearer <platform-admin-key>
Content-Type: application/json

{
  "tier_cheap_model": "groq:llama-3.1-8b",
  "tier_standard_model": "claude:claude-sonnet-5",
  "tier_premium_model": "claude:claude-opus-5"
}
```

### Desde la UI de admin

El panel de admin (`/admin`) muestra la política de routing en la columna de workspaces y permite cambiarla via `PATCH /admin/workspaces/{id}` con el campo `cost_routing_policy`.

La sección **Billing Rates** incluye un card de "Modelos de tier para cost routing" donde se configuran los modelos de cada tier.

## Desde la UI de workspace

En **Settings → LLM**, una vez seleccionado un workspace, aparece la tarjeta **Routing de costes automático** con los tres botones: Ninguno, Economy, Auto.

## Ejemplo: ahorro con `economy`

Un workspace con 5 agentes donde antes todos usaban `claude:claude-opus-5` pasa a usar `groq:llama-3.3-70b-versatile` con política `economy`. Reducción de coste ~10x para tareas de formateo y extracción que no necesitan razonamiento avanzado.

## Ejemplo: calidad selectiva con `auto`

Un `LegalReviewTeam` con `ValidatorAgent`, `LegalDraftAgent`, y `SummarizerAgent`:
- `ValidatorAgent` → cheap (groq)
- `LegalDraftAgent` → premium (claude-opus) — contiene "legal"
- `SummarizerAgent` → cheap (groq) — contiene "summarizer"
- `default` → premium (el equipo legal es tier premium)

Los agentes sin override explícito en `agent_models` heredan el tier de su nombre.
