# Análisis de impacto: AntCrew open-core self-hosted + modos de ejecución unificados

> **Estado:** borrador v0.1 — pendiente de revisión  
> **Fecha:** 2026-10-03  
> **Reglas de lectura:**  
> – Toda afirmación sobre el estado actual cita `ruta/archivo.py:línea`.  
> – Las secciones de *propuesta* describen contratos, no código implementado.  
> – Font Jardineria no puede romperse.

---

## 1. Resumen ejecutivo

AntCrew Platform gestiona hoy tres modos de ejecución con interfaces divergentes: el **pipeline visual** (agentes LangGraph por nodo), el **engine** (bucle de capacidades) y el **quick/custom** (specs inline). Esta divergencia está endurecida en cuatro archivos de servicio independientes (`runner.py`, `runner_core.py`, `engine_runner.py`, `runner_pipeline.py`) y en el schema de la API REST, lo que crea fricción para cualquier cambio transversal (BYOK, TraceLog, presupuesto, cancelación).

El objetivo de este análisis es evaluar la viabilidad de:

1. **R-A1 — Interfaz de runner unificada**: un único punto de entrada que enrute a los tres backends con contrato idéntico.  
2. **R-B1 — Selección automática de modo** (`execution: auto | pipeline | engine`): el cliente declara qué quiere ejecutar, la plataforma elige el runner óptimo.  
3. **R-C1 — HITL por tipo de artefacto** en modo engine: el revisor asignado depende del artefacto producido, no del agente.  
4. **R-D1 — Frontera open-core**: separar funciones enterprise en `ee/` bajo licencia distinta sin romper el núcleo OSS.

Veredicto preliminar: **R-A1 y R-C1 son aditivos y de bajo riesgo**. **R-B1 requiere lógica de inferencia no trivial** (fan-out, límites de revisión). **R-D1 es el cambio estructural más grande** y debe ir en fase propia porque implica mover código de `app/` al directorio `ee/` con un seam de importación.

La restricción más importante es **no romper Font Jardineria**, que usa el pipeline visual actual con HITL vía `PlatformChannel` (`app/core/channel.py`).

---

## 2. Mapa del estado actual: divergencias entre runners

### 2.1 Inventario de runners

| Runner | Archivo principal | Función de dispatch | Función sync |
|--------|-------------------|--------------------|----|
| Pipeline (named teams) | `app/services/runner.py` | `dispatch()` :55 | `_run_sync()` en `runner_core.py:339` |
| Pipeline (visual/custom/quick) | `app/services/runner_pipeline.py` | `dispatch_pipeline()`, `dispatch_custom()` :206, `dispatch_quick()` :83 | `_run_pipeline_sync()` :324, `_run_custom_sync()` :159, `_run_quick_sync()` :57 |
| Engine (capacidades) | `app/services/engine_runner.py` | `dispatch_engine()` :589 | `_run_engine_sync()` :437 |

### 2.2 Tabla de divergencias por dimensión

| Dimensión | Pipeline (named teams) | Pipeline (visual/custom) | Engine |
|-----------|------------------------|--------------------------|--------|
| **Emisión de `pipeline.start`** | SDK lo emite internamente vía bus | Listener en `dispatch_*()` espera el evento para capturar `run_id` (`runner_pipeline.py:119-122`) | `dispatch_engine()` lo emite explícitamente antes de devolver (`engine_runner.py:681-691`) |
| **Captura de `run_id`** | `asyncio.Future` bloqueado hasta `pipeline.start` (`runner_core.py:_DISPATCH_TIMEOUT`) | Ídem, con `asyncio.shield()` (`runner_pipeline.py:150`) | `new_run_id()` llamado antes del lanzamiento (`engine_runner.py:676`) |
| **ThreadPoolExecutor** | Compartido `_executor` importado de `runner_core` | Compartido vía import (`runner_pipeline.py:26`) | Propio `_executor` con prefijo `antcrew-engine` (`engine_runner.py:45`) |
| **HITL — mecanismo de bloqueo** | `concurrent.futures.Future` vía `PlatformChannel` (`app/core/channel.py`) — resuelto por `POST /reviews/:id` | Ídem para custom/visual (`runner_pipeline.py:189-201`) | `threading.Event` en dict `_engine_reviews` (`engine_runner.py:119-134`) — resuelto por `resolve_engine_review()` |
| **HITL — detección** | `approval_required=True` en agente o `force_hitl=True` en request → `team.run_interactive()` (`runner_core.py:441-447`) | Mismo patrón para custom (`runner_pipeline.py:189-203`). Visual: flag `hitl` por nodo (`runner_pipeline.py:337`) | Explícito: `hitl_after: list[str]` — instancia `HitlReviewer` en el registry (`engine_runner.py:530-554`) |
| **Cancelación** | No implementada en `runner_core.py` (señal queda pendiente en SDK) | No implementada | `threading.Event` + `_cancel_events` dict (`engine_runner.py:51-60`) |
| **BYOK** | `resolve_workspace_llm_config()` en `dispatch()` (`runner.py:~230`) | Ídem en cada `dispatch_*` (`runner_pipeline.py:247-248`) | Ídem en `dispatch_engine()` (`engine_runner.py:664`) |
| **Docs S3 config** | `_docs_config` como argumento 19 a `_run_sync()` (`runner.py:299-302`) | No presente en quick/custom; presente en visual vía `pipeline_id` lookup | Inyectado en `_run_engine_sync()` vía `DocumentationManager` (`engine_runner.py:509-521`) |
| **TraceLog** | Inicializado en `runner_core.py:376-383` vía `ANTCREW_TRACELOG_PATH` | No soportado | No soportado |
| **Resultado almacenado** | `_store_result()` acepta `RunResult` (from `team.run()`) o `dict` (from `team.run_interactive()`) (`runner_core.py:544`) | Ídem importado (`runner_pipeline.py:29`) | `_store_engine_state()` propio (`engine_runner.py:831-900`) |
| **Atribución al workspace** | `_set_run_attribution()` tras dispatch (`runner_core.py`) | Ídem, importado (`runner_pipeline.py:288`) | `_set_run_attribution()` propio (`engine_runner.py:799-828`) — schema idéntico |
| **Semáforo por workspace** | `_get_workspace_semaphore(workspace_id)` (`runner_core.py`) | Ídem importado | No implementado |
| **Presupuesto workspace** | `_check_workspace_budget()` + `_mark_workspace_budget_status()` de `runner_base` | Ídem | Ídem (`engine_runner.py:632-634, 783`) |
| **Coste real en tiempo real** | No emitido mid-run | No emitido mid-run | `pipeline.cost_update` tras cada `agent.end` (`engine_runner.py:731-746`) |

### 2.3 Dependencia de versión del SDK

`antcrew-platform/pyproject.toml` o `requirements.txt` tiene `antcrew>=0.35.0`. Esta restricción es **demasiado abierta**: el runner pipeline usa `QuickTeam`, `CustomTeam`, `TemplateAgent` introducidos en versiones recientes; el engine usa `antcrew_engine` de forma separada. Un bumping de `antcrew` que cambie el contrato de `team.run_interactive()` rompe silenciosamente el HITL de Font Jardineria sin que ningún test de integración lo detecte actualmente.

**Impacto en R-A1:** cualquier interfaz unificada debe pinear `antcrew` a un rango estrecho (`>=0.35.0,<0.40.0` como mínimo) con un changelog de breaking changes antes de ampliar.

---

## 3. Matriz de requisitos

### R-A1 — Interfaz de runner unificada

**Descripción:** Único punto de entrada (`dispatch_run(...)`) que acepta un campo `mode: "pipeline" | "engine" | "quick" | "custom"` y enruta al backend correcto. Todos los backends comparten la misma cadena BYOK, presupuesto, atribución y semáforo.

**Estado actual:** Cuatro funciones de dispatch independientes. Los comportamientos de BYOK, presupuesto y atribución están duplicados en cada uno.

**Código afectado:**
- `app/services/runner.py:55` — `dispatch()` (named teams)
- `app/services/runner_pipeline.py:83,206` — `dispatch_quick()`, `dispatch_custom()`
- `app/services/engine_runner.py:589` — `dispatch_engine()`

**Riesgo:** Medio. Refactor de plumbing, no de lógica. El riesgo real es romper el contrato de `pipeline.start` → `run_id` que Font Jardineria espera.

**Restricción Font Jardineria:** El flujo `dispatch()` → `runner_core._run_sync()` → `team.run_interactive()` → `PlatformChannel` → `POST /reviews/:id` debe seguir funcionando exactamente igual. Una envoltura no puede cambiar el timing del `pipeline.start` event.

**Restricción de versión SDK:** Pinear `antcrew` antes de unificar (`>=0.35.0,<0.40.0`).

---

### R-B1 — Selección automática de modo (`execution: auto`)

**Descripción:** Cuando `execution: auto`, la plataforma inspecciona el request e infiere el modo óptimo. Heurística base: si el request referencia un `pipeline_id` o `steps` → pipeline; si hay `goal` sin `steps` → engine; si hay `specs` inline → quick.

**Estado actual:** No existe. El cliente debe elegir el endpoint correcto (`/run/`, `/run/pipeline/`, `/engine/run/`).

**Complejidad:** Alta. La selección automática requiere resolver casos ambiguos:
- Fan-out en pipeline visual: múltiples nodos paralelos no son reemplazables por engine sin cambios de comportamiento.
- Límites de revisión: engine tiene `hitl_max_rejections` (`engine_runner.py:557`); pipeline no tiene ese concepto.
- TraceLog: solo soportado en named-team pipeline (`runner_core.py:376-383`); engine no.

**Impacto:** Requiere un analizador de request que produzca una decisión auditada. Esa lógica es nueva superficie de código.

**Recomendación:** Implementar R-B1 *después* de R-A1. En primera iteración, `execution: auto` puede ser simplemente un alias de `pipeline` para no añadir riesgo.

---

### R-C1 — HITL por tipo de artefacto (engine mode)

**Descripción:** En lugar de `hitl_after: ["architect"]` (nombre de capacidad), el cliente declara `hitl_rules: [{"artifact_kind": "ARCHITECTURE", "assignee": "lead"}, {"artifact_kind": "SOURCE", "assignee": "dba"}]`. La plataforma mapea el kind al `cap_name` correcto e instancia el `HitlReviewer`.

**Estado actual:** `hitl_after` acepta nombres de capacidad hardcodeados. El `_make_review_callback()` pasa `cap_name` al bus event como `agent_name: f"engine:{cap_name}"` (`engine_runner.py:153`). El `_patch_downstream_needs()` basa el parche en el nombre de capacidad (`engine_runner.py:177-199`).

**Código afectado:**
- `engine_runner.py:530-554` — wiring de `HitlReviewer`
- `engine_runner.py:137-174` — `_make_review_callback()`
- `engine_runner.py:177-199` — `_patch_downstream_needs()`

**Riesgo:** Bajo-medio. El mecanismo de `threading.Event` no cambia. Solo se añade una capa de traducción `artifact_kind → cap_name` antes de llamar al código existente.

**Dependencia en SDK:** Requiere que `antcrew_engine.ArtifactKind` sea estable. No introducir hasta confirmar que el enum no cambiará en la versión próxima del SDK.

---

### R-D1 — Frontera open-core y directorio `ee/`

**Descripción:** Separar las funciones enterprise en `app/ee/` con licencia distinta. El núcleo OSS (`app/`) importa de `ee/` solo a través de un seam opcional (protocolo o factory callable registrado en startup).

**Candidatos a `ee/`** (funciones sin análogo OSS razonable):
- SSO / SAML (`app/api/auth.py` — si existe SSO)
- RBAC multi-workspace avanzado (roles más allá de `admin/read`)
- Audit log BYOK detallado (`app/api/workspaces_byok_audit.py`)
- Analytics workspace (`app/api/workspaces.py` sección `/analytics`)
- Air-gap mode (sin llamadas externas al LLM provider)
- Multi-workspace per-user (si existe)

**Candidatos que deben permanecer en OSS:**
- `PlatformChannel` (`app/core/channel.py`) — usado por Font Jardineria
- `runner_core.py`, `engine_runner.py`, `runner_pipeline.py` — núcleo de ejecución
- BYOK key storage básico
- TraceLog
- Run/Ticket/Schedule CRUD

**Opciones de licencia:**

| Opción | Licencia núcleo | Licencia `ee/` | Implicación |
|--------|----------------|----------------|-------------|
| A | Apache-2.0 | AGPL-3.0 | Obliga a publicar modificaciones de ee/ si se despliega como servicio. Compatible con el modelo self-hosted. |
| B | Apache-2.0 | BSL 1.1 (change date 4 años) | Más restrictiva para competidores. Requiere comunicar la change date a clientes. |
| C | Apache-2.0 | Licencia propietaria AntCrew | Máxima flexibilidad comercial. Requiere CLA. |

**Recomendación preliminar:** Opción A si el objetivo es comunidad; Opción C si el objetivo es revenue protegido. Opción B es un compromiso razonable si el modelo de negocio es cloud-managed vs self-hosted.

**Riesgo técnico:** Mover `ee/` implica cambiar imports en `app/`. Si no se hace con un seam (ej. `app/ee_hooks.py` con callables opcionales), cualquier instalación OSS que no tenga `ee/` fallará en import. El patrón `try: from app.ee import X except ImportError: X = None` es frágil pero viable.

---

## 4. Impacto en repositorios

### 4.1 `antcrew` (SDK Python)

| Cambio | Motivación | Riesgo |
|--------|-----------|--------|
| Pinear versión en antcrew-platform a `>=0.35.0,<X.Y.0` | R-A1: contrato estable antes de unificar | Bajo — solo lockfile |
| Exponer `TraceLog` como primer ciudadano en `__init__.py` | R-A1: engine runner no tiene TraceLog | Medio — implica que antcrew-engine también lo adopte |
| Publicar changelog de breaking changes para `team.run_interactive()` | R-A1: Font Jardineria depende de este método | Bajo — es documentación |

### 4.2 `antcrew-platform`

| Cambio | Archivos | Riesgo |
|--------|----------|--------|
| R-A1: función `dispatch_run()` unificada | Nuevo `app/services/dispatch.py` + tocar los 3 dispatcher existentes | Medio |
| R-C1: `hitl_rules` en schema de engine request | `app/api/engine.py` (schema Pydantic) + `engine_runner.py:437-580` | Bajo-medio |
| R-D1: directorio `app/ee/` con seam de import | `app/ee/__init__.py` + refactor de 3-5 módulos API | Alto |
| Pinear antcrew | `pyproject.toml` o `requirements.txt` | Bajo |

**Restricción Font Jardineria:** Los tests de integración actuales en `tests/test_pipeline.py` y los E2E en `tests/e2e/` deben pasar sin cambios después de R-A1. Si algún test falla, el cambio no entra.

### 4.3 `antcrew-engine`

| Cambio | Motivación | Riesgo |
|--------|-----------|--------|
| Añadir `ArtifactKind`-to-`cap_name` mapping público | R-C1 | Bajo si el enum es estable |
| Soporte de TraceLog en EngineLoop | R-A1 (paridad con pipeline) | Medio — requiere coordinación con SDK |

### 4.4 `antcrew-docs`

Todo cambio de API (R-A1, R-C1) requiere actualización de:
- `docs/platform/api-reference.md`
- `docs/architecture/runners.md` (si existe)
- Guía de self-hosted (R-D1)

---

## 5. Propuesta de interfaz unificada (contrato, no implementación)

### 5.1 Contrato de request

```
POST /run/
{
  "request": "...",
  "execution": "auto" | "pipeline" | "engine" | "quick" | "custom",

  // pipeline fields (existing)
  "team": "DevTeam" | ...,
  "pipeline_id": "...",
  "steps": [...],
  "specs": [...],
  "force_hitl": bool,

  // engine fields (existing)
  "goal": "...",
  "tech": [...],
  "conditions": [...],
  "hitl_after": [...],

  // R-C1: nuevo
  "hitl_rules": [
    {"artifact_kind": "ARCHITECTURE", "assignee": "lead"},
    {"artifact_kind": "SOURCE", "assignee": "dba"}
  ],

  // common fields (ya existentes, ahora en un solo lugar)
  "model": "...",
  "max_cost_usd": ...,
  "max_iter": ...,
  "repo_url": "...",
}
```

### 5.2 Invariantes que deben preservarse

1. `pipeline.start` siempre se emite antes de que `dispatch_run()` devuelva el `run_id`.  
   — Actual: garantizado en pipeline vía `asyncio.Future` + `pipeline.start` listener. En engine, emitido explícitamente en `dispatch_engine():681-691`.

2. `_check_workspace_budget()` + `_mark_workspace_budget_status()` se llaman en todos los modos.  
   — Actual: duplicado en cada dispatcher. R-A1 lo centraliza.

3. `_set_run_attribution()` se llama en todos los modos.  
   — Actual: tres implementaciones casi idénticas (`runner_core.py`, `engine_runner.py:799`, `runner_pipeline.py:288`). R-A1 consolida en `runner_base.py`.

4. El semáforo por workspace se aplica solo a pipeline (no a engine).  
   — Actual: `engine_runner.py` no tiene semáforo. Decisión intencional o gap? Documentar y decidir antes de R-A1.

### 5.3 Enrutamiento en `execution: auto`

Árbol de decisión mínimo viable (primera iteración):

```
if "pipeline_id" in request  →  dispatch_pipeline()
elif "steps" in request       →  dispatch_custom()
elif "specs" in request        →  dispatch_quick()
elif "goal" in request         →  dispatch_engine()
elif "team" in request         →  dispatch()         # named teams
else                           →  422
```

En una iteración posterior, `auto` puede usar lógica de complejidad de la tarea para elegir entre pipeline y engine cuando ambos son viables.

---

## 6. Frontera open-core: qué va a `ee/` y qué permanece en OSS

### 6.1 Criterio de corte

Un módulo va a `ee/` si y solo si cumple **todos** los siguientes:
- Requiere infraestructura enterprise (IdP externo, base de datos de auditoría separada, etc.), **o**
- Es una función de diferenciación comercial directa (no reproducible trivialmente en OSS), **y**
- No es necesario para el caso de uso básico de un equipo de 1-5 personas.

### 6.2 Mapa de módulos

| Módulo | Destino | Justificación |
|--------|---------|---------------|
| `app/core/channel.py` (PlatformChannel) | OSS | Font Jardineria lo usa; es el canal mínimo viable |
| `app/services/runner_core.py` | OSS | Núcleo de ejecución |
| `app/services/engine_runner.py` | OSS | Núcleo de ejecución |
| `app/services/runner_pipeline.py` | OSS | Núcleo de ejecución |
| `app/api/workspaces_docs.py` (S3 docs) | OSS | Capacidad básica de documentación |
| `app/api/workspaces_byok.py` (BYOK key storage) | OSS | Requerido para privacy-first use case |
| `app/api/workspaces_byok_audit.py` (audit log) | `ee/` | Compliance enterprise; no crítico para uso básico |
| `app/api/workspaces.py` sección `/analytics` | `ee/` | Dashboards de negocio, no de debugging |
| SSO / SAML (si existe) | `ee/` | Infraestructura IdP enterprise |
| RBAC granular (roles más allá de admin/read) | `ee/` | Gestión multi-equipo enterprise |
| Soporte multi-workspace por usuario | `ee/` | Feature enterprise |
| Air-gap LLM proxy (si existe en `antcrew-proxy`) | `ee/` | Compliance regulatorio |

### 6.3 Seam de importación recomendado

```python
# app/ee_hooks.py  (en OSS)
from typing import Optional, Callable

_analytics_handler: Optional[Callable] = None
_audit_handler: Optional[Callable] = None

def register_analytics(fn: Callable) -> None:
    global _analytics_handler
    _analytics_handler = fn

def register_audit(fn: Callable) -> None:
    global _audit_handler
    _audit_handler = fn
```

```python
# app/ee/__init__.py  (en ee/)
from app.ee_hooks import register_analytics, register_audit
from app.ee.analytics import analytics_handler
from app.ee.audit import audit_handler

register_analytics(analytics_handler)
register_audit(audit_handler)
```

```python
# app/main.py  (en OSS)
try:
    import app.ee  # noqa: F401  — registra hooks si está instalado
except ImportError:
    pass  # ee/ no presente en instalación OSS
```

Esto mantiene `app/` completamente importable sin `ee/`.

---

## 7. Plan de fases

### Fase 0 — Preparación (sin cambios de comportamiento)

1. Pinear `antcrew>=0.35.0,<0.40.0` en `pyproject.toml`.
2. Añadir test de integración que verifique que Font Jardineria (pipeline HITL) sigue funcionando. Este test es la red de seguridad de todas las fases siguientes.
3. Documentar el semáforo de workspace: ¿debe aplicarse al engine? Decisión explícita antes de R-A1.
4. Crear `app/services/runner_base.py` si no existe ya con `_check_workspace_budget`, `_mark_workspace_budget_status`, `_set_run_attribution` consolidados (hoy en `runner_core.py` y replicados en `engine_runner.py:799`).

**Criterio de salida:** todos los tests existentes pasan.

### Fase 1 — R-A1: Interfaz unificada (bajo riesgo)

1. Crear `app/services/dispatch.py` con `dispatch_run()` como wrapper thin.
2. Añadir campo `execution` al schema de `/run/`.
3. Enrutar según árbol de decisión de §5.3.
4. Los dispatchers existentes no cambian internamente.
5. Tests: pasar todos los tests existentes + nuevo test para `execution: auto`.

**Riesgo de Font Jardineria:** Ninguno si el wrapper no toca el timing del `pipeline.start` event.

### Fase 2 — R-C1: HITL por artifact kind (bajo-medio riesgo)

1. Añadir `hitl_rules` al schema de `POST /engine/run/` y al nuevo `dispatch_run()`.
2. Añadir función `_artifact_kind_to_cap_name()` en `engine_runner.py` que mapea `ArtifactKind → str`.
3. En `_run_engine_sync()`, si `hitl_rules` está presente, construir `hitl_after` dinámicamente.
4. Tests: parametrizar `hitl_rules` con los kinds principales (`ARCHITECTURE`, `SOURCE`, `TEST`).

**Sin cambios en Font Jardineria:** Font Jardineria usa pipeline, no engine.

### Fase 3 — R-D1: Frontera open-core (alto riesgo, fase propia)

1. Crear `app/ee_hooks.py` con el seam de hooks.
2. Mover módulos de la lista §6.2 a `app/ee/`.
3. Añadir `try: import app.ee except ImportError: pass` en `app/main.py`.
4. Configurar CI con dos entornos: con `ee/` y sin `ee/`.
5. Elegir licencia (decisión de negocio, ver §9).

**Riesgo:** Este es el único cambio que puede romper imports. Requiere revisión de todos los `from app.api.X import Y` en la base de código antes de ejecutar.

### Fase 4 — R-B1: Selección automática avanzada (medio riesgo)

Solo después de Fase 1. Añadir lógica de inferencia más sofisticada en `dispatch_run()` para casos donde ni `pipeline_id` ni `steps` ni `goal` están presentes pero el request tiene suficiente semántica para elegir.

---

## 8. Diseño del experimento R-D1

### Objetivo

Validar que la separación OSS/`ee/` no rompe ningún flujo existente, con Font Jardineria como caso de prueba primario.

### Protocolo

1. **Baseline:** ejecutar la suite completa (`pytest tests/`) en la rama `main` actual. Guardar el resultado como referencia.

2. **Rama `experiment/ee-boundary`:**
   - Crear `app/ee_hooks.py` vacío.
   - Crear `app/ee/__init__.py` que registra los hooks de analytics y audit.
   - Mover `app/api/workspaces_byok_audit.py` a `app/ee/audit.py` como primer módulo.
   - Añadir `try: import app.ee` en `app/main.py`.

3. **Test sin `ee/`:** instalar la plataforma sin `app/ee/` (renombrar el directorio o excluirlo del PYTHONPATH). Ejecutar la suite completa. **Criterio de éxito:** mismo resultado que el baseline, incluyendo todos los tests de Font Jardineria.

4. **Test con `ee/`:** instalar con `app/ee/`. Ejecutar la suite completa + tests nuevos para audit log. **Criterio de éxito:** todos los tests pasan.

5. **Métricas:**
   - Tiempo de startup de la app con y sin `ee/`.
   - Número de imports que fallan sin `ee/` (objetivo: 0).
   - Cobertura de tests de los módulos movidos (objetivo: ≥ cobertura actual).

### Criterio de abort

Si en el paso 3 algún test de Font Jardineria falla, la fase se cancela y se analiza el import path que introdujo la dependencia.

---

## 9. Riesgos

| ID | Riesgo | Probabilidad | Impacto | Mitigación |
|----|--------|-------------|---------|------------|
| R01 | `antcrew` bumping rompe `team.run_interactive()` | Media | Alto (rompe Font Jardineria) | Pinear versión (Fase 0) |
| R02 | `execution: auto` elige modo incorrecto para un request ambiguo | Alta | Medio (resultado inesperado, no crash) | Logs explícitos del modo elegido; campo `execution_chosen` en respuesta |
| R03 | Seam de `ee/` introduce import circular | Media | Alto (crash en startup) | Test de importación pura sin inicializar la app |
| R04 | `antcrew_engine.ArtifactKind` enum cambia en nueva versión | Media | Medio (R-C1 falla silenciosamente) | Pinear `antcrew-engine` antes de R-C1 |
| R05 | El semáforo de workspace no se aplica al engine y causa overrun de costes | Baja | Alto | Decisión explícita en Fase 0 antes de R-A1 |
| R06 | Licencia AGPL-3.0 en `ee/` disuade a potenciales integradores enterprise | Media | Medio | Evaluar BSL o propietaria (decisión de negocio) |
| R07 | TraceLog no portado al engine hace que R-A1 sea asimétrico en observabilidad | Alta | Bajo-medio | Documentar la asimetría; portarlo en Fase 2 o 3 |

---

## 10. Decisiones abiertas

| ID | Decisión | Opciones | Bloquea |
|----|---------|---------|---------|
| D01 | ¿Licencia de `ee/`? | Apache-2.0 / AGPL-3.0 / BSL 1.1 / Propietaria | R-D1 |
| D02 | ¿El semáforo de workspace aplica al engine? | Sí / No / Configurable por workspace | R-A1 |
| D03 | ¿TraceLog en engine runner? | Sí (Fase 2) / No (asimetría aceptada) / Diferido | R-A1 paridad |
| D04 | ¿`execution: auto` en primera iteración es simplemente `pipeline` o tiene lógica real? | Alias / Árbol mínimo §5.3 / ML-based (diferido) | R-B1 |
| D05 | ¿`hitl_rules` reemplaza `hitl_after` o coexiste? | Reemplaza / Coexiste como alias | R-C1 schema |
| D06 | ¿Qué versión de `antcrew` se pineará en Fase 0? | `<0.40.0` / `<1.0.0` / Semver estricto | Fase 0 |
| D07 | ¿El engine runner necesita semáforo por workspace antes de R-A1? | Sí urgente / No hasta R-A1 | Fase 0 |

---

*Fin del análisis. Este documento no contiene código implementable; todas las citas referencian el estado actual del código en producción.*
