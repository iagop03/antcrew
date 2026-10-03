# Documentation S3 Integration

Connect an S3 bucket to a workspace so agents automatically receive relevant documentation context on every run. The platform indexes your docs on demand and injects matching content into each capability's LLM prompt via the `DocumentationManager`.

---

## How it works

1. You configure an S3 bucket + optional schema in **Settings → Documentation**.
2. Trigger **Re-index** to pull all documents from S3 and build the semantic index.
3. On every engine run for that workspace, the platform rebuilds the `DocumentationManager`, indexes from S3, and calls `set_documentation(mgr)` on each registered capability before the loop starts.
4. Inside each LLM prompt, the capability calls `get_context_for_agent(agent_name, query)` and prepends the most relevant doc chunks.

No changes are needed to your pipeline code — docs context is injected transparently.

---

## S3 configuration

In **Settings → Documentation**, select a workspace and fill in:

| Field | Description |
|---|---|
| **Bucket** | S3 bucket name (required) |
| **Prefix** | Optional key prefix, e.g. `docs/project-a/` |
| **Region** | AWS region (default: `us-east-1`) |
| **Access Key ID** | AWS access key ID — leave empty to use the server's IAM role |
| **Secret Access Key** | AWS secret access key |

Credentials are encrypted with Fernet before being stored. Leave both key fields empty to rely on the server's IAM role or instance profile.

### REST API

```bash
# Get current config (keys are never returned, only presence is indicated)
GET /workspaces/{id}/docs/config

# Set config
PUT /workspaces/{id}/docs/config
{
  "bucket": "my-company-docs",
  "prefix": "project-a/",
  "region": "us-east-1",
  "access_key": "AKIA...",
  "secret_key": "..."
}

# Clear config
DELETE /workspaces/{id}/docs/config
```

---

## Schema YAML

The schema tells the `DocumentationManager` what types of documents exist, which agents should use them, and how to classify files automatically.

```yaml
documentation_schema:
  org_name: Acme Corp

  document_types:
    - id: spec
      name: Product Specification
      category: product
      parser: markdown
      agents: [Architect, TaskPlanner]

    - id: adr
      name: Architecture Decision Record
      category: technical
      parser: markdown
      agents: [Architect, CodeReviewer]

    - id: runbook
      name: Operations Runbook
      category: operational
      parser: markdown

  path_rules:
    - prefix: "specs/"
      doc_type: spec
    - prefix: "adrs/"
      doc_type: adr
    - prefix: "runbooks/"
      doc_type: runbook

  query_hints:
    - pattern: "auth|authentication|login"
      doc_types: [spec, adr]
    - pattern: "deploy|rollback|incident"
      doc_types: [runbook]
```

Save the schema in **Settings → Documentation → Schema YAML**. It is validated as valid YAML before saving.

### REST API

```bash
# Get current schema
GET /workspaces/{id}/docs/schema

# Set schema
PUT /workspaces/{id}/docs/schema
{"schema_yaml": "documentation_schema:\n  ..."}
```

---

## Uploading documents

Upload files directly from the UI (**Settings → Documentation → Upload**) or via the API:

```bash
curl -X POST https://antcrew.org/workspaces/{id}/docs/upload \
  -H "X-Api-Key: acw_live_..." \
  -F "file=@spec.md" \
  -F "doc_type=spec"
```

Supported file types: `.md`, `.txt`, `.docx`, `.pdf`, `.json`, `.cbl`, `.cob`, `.cpy`.

The file is stored at `{prefix}{doc_type}/{filename}` in S3 with an antcrew sidecar metadata entry.

---

## Listing and deleting documents

```bash
# List all doc_ids in the bucket
GET /workspaces/{id}/docs

# Delete a document
DELETE /workspaces/{id}/docs/{doc_id:path}
```

From the UI, each document in the list has a delete button.

---

## Re-indexing

The index is built on demand (not continuously). Trigger a re-index:

- **UI**: click **Re-index S3** in **Settings → Documentation → Documents**.
- **API**: `POST /workspaces/{id}/docs/index`

```bash
curl -X POST https://antcrew.org/workspaces/{id}/docs/index \
  -H "X-Api-Key: acw_live_..."
# → {"indexed": 12, "doc_ids": ["spec/auth.md", "adr/001-jwt.md", ...]}
```

The re-index call is synchronous — it downloads every document, parses it, and builds the semantic index before returning. For large buckets this may take 10–30 seconds; call it in the background or from a CI step after uploading new docs.

---

## Automatic injection per run

When a workspace has S3 docs configured, **every run** for that workspace — whether via a pre-built team (DevTeam, FullStackTeam, etc.) or an engine capability loop — automatically:

1. Builds a fresh `DocumentationManager` with the workspace's S3 credentials and schema.
2. Calls `index_from_storage()` to download and parse all docs.
3. Calls `set_documentation(mgr)` on every agent or capability that supports it.
4. Each agent uses `get_context_for_agent(agent_name, query)` to prepend the most relevant doc chunks to its LLM prompt.

No changes are needed to your pipeline or team code — docs context is injected transparently.

A failed docs setup (S3 unreachable, bad credentials) is logged as a warning and the run continues without docs context — it does not block the pipeline.

---

## IAM policy (minimum permissions)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:HeadObject"
      ],
      "Resource": [
        "arn:aws:s3:::my-company-docs",
        "arn:aws:s3:::my-company-docs/*"
      ]
    }
  ]
}
```

The platform only needs read + write access to the configured bucket. No other AWS permissions are required.

---

## COBOL and legacy docs

For teams on IBM AS/400 (iSeries) with COBOL source, set `org_type: legacy` in the schema and point the prefix at your copybook directory. The `CobolParser` handles fixed-format and free-format COBOL automatically.

See [Legacy / COBOL Support](../engine/legacy-cobol.md) for the full schema reference and `AS400Connector` details.
