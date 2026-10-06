# Field-level encryption

AntCrew Platform encrypts sensitive JSON columns at the application layer using **AES-256-GCM** when `ANTCREW_ENCRYPTION_KEY` is set.

## What is encrypted

| Column | Table | Sensitivity |
|--------|-------|-------------|
| `config_json` | `ticket_destination` | API tokens for GitHub/Linear/Jira |
| `state` | `run` | Full typed artifacts from pipeline runs |

When the key is not set, the platform writes plain JSON and behaves exactly as before — no schema change, no migration required.

## Setup

Generate a key (do this once, store in your secrets manager):

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Set it as an environment variable before starting the platform:

```bash
ANTCREW_ENCRYPTION_KEY=<base64-32-bytes>
```

## Backward compatibility

Columns written before the key was set are plain JSON. The `EncryptedJSON` type detects on read whether a value is encrypted (sentinel prefix `antcrew:enc:v1:`) or plain JSON, and handles both transparently. Existing data continues to work; new writes are encrypted.

## Key rotation

To rotate the key:

1. Decrypt all existing rows with the old key
2. Re-encrypt with the new key
3. Set `ANTCREW_ENCRYPTION_KEY` to the new key

There is no built-in rotation command yet — open an issue or do it in a one-off migration script using `EncryptedJSON._encrypt` / `EncryptedJSON._decrypt`.

## Regulated environments

For healthcare (HIPAA) or financial (PCI-DSS) deployments, combine field encryption with:

- PostgreSQL with TLS (`sslmode=verify-full`)
- Disk encryption on the host (dm-crypt, LUKS, or cloud provider managed)
- Network isolation (VPC, private subnets)

AES-256-GCM provides authenticated encryption: tampering with the ciphertext causes an explicit `cryptography.exceptions.InvalidTag` error rather than silently returning garbage.

---

## See also

- [Compliance Pack](compliance-pack.md) — bulk attestation export and compliance officer dashboard
- [Run attestation](attestation.md) — governance hash and HMAC-signed provenance documents
- [Data retention](data-retention.md) — retention policies and GDPR erasure
