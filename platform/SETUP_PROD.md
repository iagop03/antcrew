# Setup PROD — pasos pendientes

Código ya implementado y commitado. Solo falta configuración externa.

---

## 1. GitHub Secrets

Ir a: **repo antcrew-platform → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Cómo obtenerlo |
|--------|----------------|
| `B2_KEY_ID` | Backblaze B2 → App Keys → Create Application Key → copiar Key ID |
| `B2_APPLICATION_KEY` | Mismo paso → copiar applicationKey (solo se muestra una vez) |
| `B2_ENDPOINT_URL` | Backblaze → Bucket → Endpoint, p.ej. `https://s3.us-west-004.backblazeb2.com` |
| `B2_BACKUP_BUCKET` | Nombre del bucket creado en Backblaze para backups de DB |
| `ARTIFACT_STORAGE_URL` | `s3://nombre-del-bucket-de-artefactos` (puede ser el mismo bucket u otro) |
| `SENTRY_DSN` | sentry.io → Projects → New Project → Python → copiar DSN |
| `REDIS_URL` | upstash.com → Redis → Create Database → copiar URL `rediss://default:...@...upstash.io:6379` |
| `ANTCREW_ENCRYPTION_KEY` | Ejecutar: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PROD_SERVER_IP` | Se obtiene tras el paso 3 (Terraform) |
| `HETZNER_SSH_PRIVATE_KEY` | Contenido de `~/.ssh/id_ed25519` (o la clave que uses para Hetzner) |

---

## 2. Upptime

Repo: `antcrew-upptime` (ya existe con el workflow configurado)

1. Ir a **repo antcrew-upptime → Settings → Secrets → New repository secret**
2. Añadir `UPPTIME_GH_TOKEN`: un PAT de GitHub con permisos `repo`
   - GitHub → Settings → Developer settings → Personal access tokens → Fine-grained → permisos `Contents: read+write` en el repo antcrew-upptime

---

## 3. Terraform — servidor Hetzner

Requisitos:
- `terraform` instalado (`brew install terraform` / `choco install terraform`)
- Token de Hetzner Cloud (hetzner.com → proyecto → Security → API Tokens → Create Token con Read+Write)

```bash
cd antcrew-platform/terraform

terraform init

terraform apply \
  -var="hcloud_token=TU_TOKEN_HETZNER" \
  -var='ssh_public_key=contenido de ~/.ssh/id_ed25519.pub'
```

Tras el apply, copiar el output `floating_ip` y:
1. Añadirlo como secret `PROD_SERVER_IP` en GitHub (paso 1)
2. Actualizar el A record de `antcrew.org` en Cloudflare → `floating_ip`

---

## 4. Activar RLS (cuando estés listo)

Una vez que confirmes que todos los runs en prod tienen `workspace_id` correcto:

Añadir a `.env.prod` (o como variable en el compose):
```
ANTCREW_ENABLE_RLS=true
```

La migración 073 ya está aplicada — el flag la activa por primera vez.

---

## 5. Backblaze B2 — crear bucket y key

1. backblaze.com → Create Account (o login)
2. Buckets → Create a Bucket → nombre: `antcrew-backups` (privado)
3. App Keys → Add a New Application Key:
   - Bucket: `antcrew-backups`
   - Access: Read and Write
4. Copiar `keyID` → secret `B2_KEY_ID`
5. Copiar `applicationKey` → secret `B2_APPLICATION_KEY`
6. Ir al bucket → Endpoint → copiar URL → secret `B2_ENDPOINT_URL`
7. `B2_BACKUP_BUCKET` = `antcrew-backups`

---

## 6. Sentry — crear proyecto

1. sentry.io → Create Account (o login)
2. Projects → Create Project → Platform: Python (FastAPI)
3. Copiar el DSN que aparece en el setup → secret `SENTRY_DSN`
4. El plan gratuito (5k errores/mes) es suficiente para empezar

---

## 7. Upstash Redis — crear instancia

1. upstash.com → Create Account → Redis → Create Database
2. Región: EU (Frankfurt o Ireland para menor latencia desde Hetzner nbg1)
3. Type: Regional (más barato) o Global (más resiliente)
4. Connect → copiar la URL `rediss://default:...@...upstash.io:6379` → secret `REDIS_URL`

---

## 8. TraceLog — montar en volumen persistente

Por defecto `ANTCREW_TRACELOG_PATH=./antcrew_trace.db` apunta al filesystem del contenedor.
Un `docker compose down` + `up` lo borra. Añadir la variable al servicio `platform` en `.env.prod`:

```
ANTCREW_TRACELOG_PATH=/data/antcrew_trace.db
```

`/data` ya es el mount del volumen Hetzner (`antcrew_data:/data`) definido en `docker-compose.prod.yml`.
La rotación gzip también escribe en ese path — sin el volumen, las rotaciones desaparecen al restart.

---

## 9. Verificar field encryption en prod

Tras arrancar el servidor, comprobar en los logs que **no** aparece el warning:

```
slack_hitl: SLACK_TOKEN_ENCRYPTION_KEY not set — storing Slack token in plain text
```

Si aparece, `ANTCREW_ENCRYPTION_KEY` no está llegando al contenedor. Verificar que está en `.env.prod`
y que `docker-compose.prod.yml` lo incluye en la sección `environment` del servicio `platform`.
