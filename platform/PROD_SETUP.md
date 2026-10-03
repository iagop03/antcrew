# Activar entorno de producción — paso a paso

El workflow `deploy.yml` ya está preparado en `main`. Solo falta crear la rama
y configurar las protecciones en GitHub.

---

## Paso 1 — Crear la rama `release`

Ejecutar desde `antcrew-platform` con `main` actualizado:

```bash
git checkout main
git pull
git checkout -b release
git push -u origin release
```

La rama se crea desde el HEAD de `main` en ese momento — ese es el código
que irá a prod por primera vez.

---

## Paso 2 — Branch protection en `release`

Impide push directo y obliga a PR con CI verde y 1 revisor:

```bash
gh api --method PUT repos/iagop03/antcrew-platform/branches/release/protection \
  --field required_status_checks='{"strict":true,"contexts":["Test (Python 3.12, SQLite)","Test (Python 3.13, SQLite)","Tests (PostgreSQL)"]}' \
  --field enforce_admins=false \
  --field required_pull_request_reviews='{"required_approving_review_count":1}' \
  --field restrictions=null
```

Ajustar `required_approving_review_count` si se quieren más de 1 revisor.

---

## Paso 3 — Required reviewers en el Environment "prod"

Esto añade el gate de aprobación humana antes de que el deploy a prod arranque.

**Vía UI (más fácil):**

1. GitHub → repositorio `antcrew-platform` → Settings → Environments
2. Seleccionar (o crear) el environment `prod`
3. Activar **Required reviewers**
4. Añadir los usuarios/equipos que deben aprobar cada deploy

**Vía API** (necesita el ID numérico del usuario):

```bash
# Obtener el ID numérico de un usuario
gh api users/iagop03 --jq .id

# Crear/actualizar el environment con required reviewers
gh api --method PUT repos/iagop03/antcrew-platform/environments/prod \
  --field reviewers='[{"type":"User","id":NUMERIC_ID}]'
```

Sustituir `NUMERIC_ID` por el valor devuelto en el primer comando.

---

## Paso 4 — Secrets del environment `prod`

Verificar que el environment `prod` en GitHub tiene todos los secrets necesarios
(los que usa el job de deploy PROD en `deploy.yml`):

- `HETZNER_SSH_PRIVATE_KEY`
- `PROD_SERVER_IP`
- `DATABASE_URL`
- `SECRET_KEY`
- `PLATFORM_API_KEY`
- `ANTHROPIC_API_KEY`
- `BYOK_ENCRYPTION_KEY`
- `TOTP_ENCRYPTION_KEY`  ← añadir antes de primer deploy (security fix MFA)
- `BASE_URL`
- `PLATFORM_BASE_URL`
- `PLATFORM_ADMIN_TOKEN`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- `GH_APP_ID`, `GH_APP_PRIVATE_KEY`, `GH_WEBHOOK_SECRET`
- `DEEPSEEK_API_KEY`

---

## Flujo operativo tras la activación

```
push a main ──→ CI + deploy INT  (automático)
                    │
           workflow_dispatch UAT  (manual, desde GitHub Actions)
                    │
           [validar en UAT — platform-uat.antcrew.org]
                    │
           PR: main → release  (1 aprobación + CI verde requeridos)
                    │
           merge ──→ gate verifica que UAT fue exitoso
                     │
                     Environment "prod" pausa y notifica a revisores
                     │  [aprueban en GitHub Actions UI]
                     ▼
                  deploy PROD  (antcrew.org)
```

### Flujo de hotfix urgente

```bash
# Rama desde release (no desde main)
git checkout release
git pull
git checkout -b hotfix/descripcion
# ... fix ...
git push origin hotfix/descripcion
# PR hacia release (mismo proceso: aprobación + gate)
```

---

## Notas

- El `gate-prod` en el workflow verifica automáticamente que el último deploy
  de UAT fue exitoso antes de continuar. No hace falta comprobarlo a mano.
- El environment `prod` en GitHub actúa como segundo gate: aunque el PR esté
  aprobado y mergeado, el job de deploy se pausa y envía notificación a los
  revisores configurados. Pueden aprobar o rechazar desde la UI de Actions.
- `workflow_dispatch` ya no tiene la opción `prod` — la única vía a prod es
  la rama `release`. Para emergencias extremas se puede re-añadir manualmente.
