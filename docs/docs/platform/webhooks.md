# Webhooks

Webhooks deliver real-time event notifications to your own endpoint when things happen in antcrew.

## Supported events

| Event | Trigger |
|---|---|
| `run.created` | A new run is submitted |
| `run.completed` | A run finishes successfully |
| `run.failed` | A run ends with an error |
| `ticket.created` | A ticket is extracted from a run |
| `review.created` | A HITL review is opened |
| `review.resolved` | A review is approved or rejected |

## Configuring a webhook

Go to **Webhooks** in the dashboard and click **Add webhook**. Provide:
- **URL** — your HTTPS endpoint
- **Events** — which events to subscribe to
- **Secret** — used to verify the signature on incoming requests

## Verifying signatures

Each request includes an `X-Antcrew-Signature` header. Verify it with HMAC-SHA256:

```python
import hmac, hashlib

def verify(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```
