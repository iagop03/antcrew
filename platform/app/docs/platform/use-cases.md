# Use cases

Ten escenarios reales para los que antcrew está diseñado. Cada uno incluye el input, el flujo de aprobación, el resultado y el coste.

---

## 1 — Añadir autenticación OAuth2 a una API existente

**Quién lo usa:** equipo backend de una startup fintech (8 devs)

**El problema:** tienen una API con autenticación por API key que necesita OAuth2 (GitHub + Google) sin romper la compatibilidad con los usuarios existentes. Han intentado hacerlo con Claude Code dos veces; ambas veces el modelo modificó tablas sin entender las FK constraints.

**Cómo lo resuelven con antcrew:**

```bash
antcrew issue acme/payments-api#312
```

```
◆ DISCOVERY    Detected: JWT auth, User model, 3 existing auth endpoints
               No OAuth provider table. No existing sessions table.
               15 affected modules identified.

◆ PLAN         New: OAuthProvider model + migration
               Modified: User (add oauth_id optional), auth router, tests
               Complexity: Medium · Migration: yes

  → HUMAN GATE 1
  "Migration adds nullable column — safe to roll back.
   Existing API key auth unaffected. Approve?"
  [A]pprove

◆ IMPLEMENT    BackendDev: 14 files generated
◆ TEST         187 tests · 184 passed · 3 failed → auto-retry → 187 passed
◆ REVIEW       2 findings: token expiry not validated, test coverage gap
               Both fixed by developer.

  → HUMAN GATE 2
  "187/187 tests. 2 review findings resolved. Push PR?"
  [A]pprove

◆ PR           github.com/acme/payments-api/pull/318 opened
               Cost: $2.14 · Time: 11m 32s · HITL: 2
```

**Por qué importa:** el plan mostró explícitamente que la migración era nullable y reversible antes de escribir una línea. El dev pudo aprobar con confianza sin leer el código.

---

## 2 — Migrar una API REST de Python 3.10 a 3.12

**Quién lo usa:** equipo de plataforma de un banco (regulado)

**El problema:** 47 módulos, 12.000 líneas de código, varios usos de `asyncio.get_event_loop()` y `typing.Union` deprecados. La migración manual estimada: 3 días. El riesgo real: nadie sabe cuántos sitios tienen el patrón.

```bash
antcrew run "Migrate codebase from Python 3.10 to 3.12 compatibility" \
  --project-dir . --team migration --model claude
```

```
◆ SCAN         47 modules analyzed
               Found: 23 asyncio.get_event_loop() · 67 Union[X,Y] → X|Y
               8 deprecated datetime.utcnow() · 4 pkg_resources usages
               Estimated: 312 replacements in 31 files

◆ PLAN         Auto-fixable: 287 (93%)
               Manual review needed: 25 (7%) — complex event loop patterns

  → HUMAN GATE
  "287 changes will be applied automatically.
   25 patterns flagged for manual review (list attached).
   Continue?"
  [A]pprove

◆ MIGRATE      31 files updated · 287 replacements applied
◆ TEST         412 tests · 412 passed (0 regressions)
◆ REPORT       migration-report.md generated — 25 manual items documented

Cost: $3.41 · Time: 14m 18s
```

**Resultado:** 93% automatizado. El equipo solo revisó los 25 casos complejos, ya documentados y localizados.

---

## 3 — Añadir Stripe subscriptions a un SaaS

**Quién lo usa:** founder técnico de un SaaS B2B (12 clientes, primeros 3 meses)

**El problema:** necesita un tier de pago antes de la demo del jueves. Con Cursor lo intentó y generó código que no considera idempotencia en los webhooks de Stripe.

```bash
antcrew issue my-saas/backend#28
```

El issue dice: *"Add a Pro subscription plan at $49/mo using Stripe. Users should be able to upgrade from the settings page. Cancel at end of billing period."*

```
◆ DISCOVERY    User model detected · No Subscription table · Stripe not configured
               Settings frontend: React

◆ PLAN         New: Subscription model + migration
               New: stripe_webhook handler (idempotent: StripeEvent dedup table)
               Modified: User, settings API, settings.tsx
               Complexity: Medium-High · Migration: yes (2 tables)

  → HUMAN GATE 1
  "Plan includes idempotent webhook handler (Stripe best practice).
   2 migrations — both additive. Approve?"
  [A]pprove

◆ IMPLEMENT    FullStackTeam: backend (6 files) + frontend (3 files)
◆ TEST         94 tests · 94 passed · webhook idempotency tested
◆ REVIEW       1 finding: missing webhook signature validation → fixed

  → HUMAN GATE 2
  [A]pprove

◆ PR           Cost: $2.89 · Time: 13m 04s
```

**Por qué importa:** el plan incluyó la dedup table de webhooks por cuenta propia — antcrew sabía que era necesario porque el discoverer leyó el Stripe docs pattern en la base de conocimiento.

---

## 4 — Encontrar y arreglar un bug de producción complejo

**Quién lo usa:** equipo de infraestructura de una compañía de seguros

**El problema:** los cálculos de prima están dando resultados inconsistentes en producción. El bug apareció en la release de hace 3 semanas pero nadie lo ha reproducido en local.

```bash
antcrew run "Investigate and fix inconsistent premium calculations in production" \
  --project-dir . --team dev --model claude \
  --context "Bug report: premiums differ between requests for same policy input. \
             Started after release v2.14.0. TraceLog shows different values for \
             same policy_id in the same second."
```

```
◆ DISCOVERY    Analyzed git diff v2.13.0..v2.14.0: 847 lines changed
               Premium calculation path: 3 modules, 1 shared cache
               Found: LRU cache added in v2.14.0 — key includes timestamp

◆ PLAN         Root cause: cache key includes datetime.now() component
               Fix: remove timestamp from cache key · 1 file · 2 lines
               Risk: LOW — cache invalidation only

  → HUMAN GATE
  "Root cause found: cache key accidentally includes millisecond timestamp.
   Fix is 2 lines in pricing/cache.py:47.
   Approve fix?"
  [A]pprove

◆ IMPLEMENT    2 lines changed
◆ TEST         218 tests · 218 passed · regression test added
◆ REVIEW       No findings

Cost: $0.43 · Time: 3m 11s
```

**Por qué importa:** análisis automático del diff que introdujo el bug. El plan mostró el root cause antes de tocar código.

---

## 5 — Revisión de contrato con aprobación del equipo legal

**Quién lo usa:** legaltech startup (revisión de contratos para pymes)

**El problema:** necesitan revisar NDAs en volumen, identificar cláusulas de alto riesgo y generar un informe para el abogado senior. El abogado aprueba el informe antes de que llegue al cliente.

```python
from antcrew import LegalReviewTeam
from antcrew.hitl import RemoteHITL

team = LegalReviewTeam(
    hitl=RemoteHITL(reviewer_email="senior@lawfirm.com")
)
result = team.run(nda_text)
```

```
◆ EXTRACT      12 clauses identified
◆ RISK         3 HIGH-risk: perpetual IP assignment, one-sided indemnity,
               no limitation of liability
               2 MEDIUM: auto-renewal, governing law (US only)

  → REMOTE HITL (email → senior@lawfirm.com)
  "3 high-risk clauses flagged. Review before delivering to client?"
  Senior lawyer: [APPROVE WITH MODIFICATIONS]
  Modification: "Add note on IP clause: client must negotiate explicitly"

◆ REPORT       legal-review-2026-10-07.pdf generated with lawyer annotation

Cost: $0.18 · Time: 2m 04s
```

**Resultado:** el abogado tiene trazabilidad de qué clausulas identificó el AI y qué modificó él. El informe incluye ambos niveles en el TraceLog.

---

## 6 — Añadir tests a código legacy sin tests

**Quién lo usa:** equipo de una gestoría que mantiene un sistema Python de 2015

**El problema:** 4.200 líneas de código de facturación, 0 tests. No se atreverán a tocar nada sin cobertura mínima.

```bash
antcrew run "Generate comprehensive test suite for the invoicing module" \
  --project-dir ./invoicing --team dev --model claude
```

```
◆ DISCOVERY    4.200 lines · 23 public functions · 0 existing tests
               Identified: 8 critical paths (tax calculation, rounding, VAT)

◆ PLAN         Generate: 89 unit tests + 12 integration tests
               Focus: tax calculation functions (highest risk)

  → HUMAN GATE
  "89 unit + 12 integration tests planned.
   Coverage estimate: 67% → 71%.
   Approve?"
  [A]pprove

◆ IMPLEMENT    101 tests generated (tests/test_invoicing.py)
◆ TEST         101 tests run · 98 passed · 3 failed
               3 failures reveal real bugs: rounding error in VAT, edge case
               in credit notes, off-by-one in invoice numbering

◆ REVIEW       Documented 3 bug reports (separate issues created)
               Test suite committed — bugs deferred for separate PRs

Cost: $1.12 · Time: 7m 22s
```

**Por qué importa:** los 3 tests que fallaron no eran tests mal escritos — eran bugs reales en el código de producción que estaban ahí desde 2015.

---

## 7 — Refactorizar un módulo acoplado

**Quién lo usa:** equipo de plataforma de un marketplace (40 devs)

**El problema:** el módulo de notificaciones llama directamente a la base de datos, al servicio de email y al servicio de SMS. Imposible testear. Nadie lo toca.

```bash
antcrew run "Refactor notifications module to use dependency injection and event bus" \
  --project-dir . --model claude
```

```
◆ DISCOVERY    notifications.py: 847 lines · 7 direct DB calls · 3 direct service calls
               12 callers across the codebase

◆ PLAN         Extract: NotificationService interface
               New: event bus adapter (in-memory + configurable)
               Modify: 12 callers to inject service
               Zero behavior change — all existing tests must pass

  → HUMAN GATE
  "Refactor plan: zero behavior change, 12 callers updated.
   Estimated risk: LOW (all existing tests must stay green).
   Approve?"
  [A]pprove

◆ IMPLEMENT    18 files modified
◆ TEST         334 tests · 334 passed (0 regressions)
◆ REVIEW       1 finding: missing type hint in adapter → fixed

Cost: $2.67 · Time: 12m 41s
```

---

## 8 — Generación de documentación técnica para auditoría

**Quién lo usa:** equipo de seguridad de una empresa healthcare (ISO 27001)

**El problema:** auditoría en 2 semanas. Necesitan documentación actualizada de todos los endpoints de la API, los modelos de datos, y los flujos de autenticación. Está desactualizada desde hace 6 meses.

```bash
antcrew run "Generate complete API and data model documentation for security audit" \
  --project-dir . --team dev --model claude
```

```
◆ DISCOVERY    47 endpoints · 23 models · 3 auth flows
               Existing docs: 40% outdated vs current code

◆ IMPLEMENT    docs/api-reference.md (47 endpoints documented)
               docs/data-models.md (23 models with field descriptions)
               docs/auth-flows.md (3 flows with sequence diagrams)
               docs/security-controls.md (generated from code analysis)

◆ REVIEW       Cross-referenced docs vs code: 7 discrepancies found
               All 7 corrected in the same run

Cost: $1.89 · Time: 8m 55s
```

**Resultado:** 4 documentos listos para la auditoría, generados desde el código actual, no desde la documentación antigua.

---

## 9 — Integración de Slack con aprobación de incidencias

**Quién lo usa:** equipo DevOps de una empresa de logística

**El problema:** cuando un run falla en producción, el oncall tiene que abrir manualmente una incidencia en Jira, notificar en Slack y escribir el post-mortem inicial. Proceso de 20 minutos en plena noche.

```python
from antcrew import DevTeam
from antcrew.channels import SlackChannel
from antcrew.adapters.tracker import JiraIntegration

team = DevTeam(
    channel=SlackChannel(webhook=SLACK_WEBHOOK, channel="#incidents"),
    tracker=JiraIntegration(project="OPS"),
)
result = team.run("Analyze production failure and generate incident report")
```

```
◆ ANALYZE      Root cause identified: disk full on worker-03
◆ REPORT       Incident draft: severity HIGH, affected: 847 orders
               Timeline reconstructed from logs

  → SLACK (#incidents)
  "antcrew incident draft ready
   Severity: HIGH · Orders affected: 847
   Root cause: disk full worker-03
   [APPROVE AND CREATE JIRA]  [MODIFY]"

  Oncall: [APPROVE AND CREATE JIRA]

◆ JIRA         OPS-2847 created with full timeline
◆ SLACK        #incidents updated with Jira link

Cost: $0.34 · Time: 2m 18s
```

**Por qué importa:** el oncall aprobó el draft desde Slack en el móvil, sin abrir el portátil. El Jira se creó con contexto completo.

---

## 10 — DPA + informe de retención para cliente enterprise

**Quién lo usa:** CSM de una startup que vende a healthcare enterprise (necesita GDPR Art. 28)

**El problema:** un hospital pide el DPA firmado + política de retención de datos antes de firmar el contrato. El CSM no sabe qué hay que poner.

```bash
# Desde la plataforma (requiere licencia Regulated)
curl -H "Authorization: Bearer $TOKEN" \
  "https://platform.antcrew.co/api/workspaces/42/dpa-template"
```

```
◆ GENERA       DPA prerrelleno con:
               - Nombre entidad: Antcrew Technologies SL
               - NIF: B-12345678
               - Dirección: Calle Ejemplo 1, Madrid
               - Finalidad: procesamiento de datos de IA
               - Retención: 90 días (configurado en workspace)
               - Base legal: Art. 28 GDPR

◆ RETENCIÓN    GET /workspaces/42/retention-policy
               data_retention_days: 90
               purge_on_delete: true
               encryption: AES-256-GCM activo

◆ HASH CHAIN   GET /compliance/hash-chain
               1.247 entradas verificadas · cadena íntegra · sin manipulaciones
```

**Resultado:** el CSM descarga un HTML listo para imprimir, con datos reales del workspace, que el DPO firma en 10 minutos. El cliente recibe también el informe de hash chain para verificar que sus datos no se han manipulado.

---

## Patrón común

En los 10 casos, la estructura es la misma:

1. antcrew analiza el contexto antes de actuar
2. muestra un plan antes de escribir código
3. hay al menos un punto donde un humano puede parar, pedir cambios o rechazar
4. al final hay un número: coste, tiempo, tests, findings

Ese número es lo que falta en la mayoría de herramientas de AI: no "el agente completó la tarea", sino "el agente completó la tarea en 11 minutos, por $1.84, con 2 aprobaciones humanas y 187 tests verdes".
