# Outbound Webhooks

AntCrew can POST a JSON payload to any HTTPS URL whenever a pipeline event fires. Use this to integrate pipeline runs into Slack, PagerDuty, custom dashboards, or any system that accepts webhooks.

## How it works

Every event emitted by the pipeline event bus (`pipeline.start`, `agent.start`, `agent.end`, `pipeline.end`, `hitl.pending`, `llm.fallback`, etc.) is delivered to all enabled webhook URLs registered on the workspace.

The platform queues each event as a `WebhookDelivery` row and delivers it asynchronously with automatic retry (up to 5 attempts, exponential backoff: 2 → 4 → 8 → 16 seconds).

## Configuring webhooks

### Via the dashboard

1. Open **Settings → LLM / Proxy** for your workspace
2. Scroll to **Webhooks salientes**
3. Click **Añadir**, enter the HTTPS URL and an optional label
4. Click **Guardar**

The webhook is immediately active for all future runs.

### Via the API

```bash
# Register a webhook
curl -X POST https://app.antcrew.ai/workspaces/{workspace_id}/webhooks \
  -H "Authorization: Bearer $ANTCREW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.slack.com/services/...",
    "label": "Slack notifications",
    "events": ["*"]
  }'

# List webhooks
curl https://app.antcrew.ai/workspaces/{workspace_id}/webhooks \
  -H "Authorization: Bearer $ANTCREW_API_KEY"

# Delete a webhook
curl -X DELETE https://app.antcrew.ai/workspaces/{workspace_id}/webhooks/{webhook_id} \
  -H "Authorization: Bearer $ANTCREW_API_KEY"
```

### Event filtering

The `events` field accepts a list of event type strings. Use `"*"` to receive all events (default):

```json
{ "url": "...", "events": ["*"] }
```

Or subscribe to specific events only:

```json
{ "url": "...", "events": ["pipeline.end", "hitl.pending", "llm.fallback"] }
```

## Payload format

Each delivery is a JSON POST with `Content-Type: application/json`:

```json
{
  "event_type": "pipeline.end",
  "run_id": "4c3fa8b2a1d0",
  "thread_id": "t-abc123",
  "cost_usd": 0.0234,
  "success": true,
  "artifact_summary": { "code_artifacts": 3, "test_artifacts": 2 }
}
```

The top-level `event_type` field is always present. All other fields come from the event payload and vary by event type — see the [Event Schema](../engine/event-schema.md) for the full list.

## Request signature

When the `WEBHOOK_SIGNING_SECRET` environment variable is set on the platform, each delivery includes an HMAC-SHA256 signature:

```
X-Antcrew-Signature: sha256=<hex_digest>
```

Verify it on your receiver:

```python
import hashlib, hmac

def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

## Delivery guarantees

| Property | Value |
|---|---|
| Retry attempts | 5 |
| Backoff | 2ˢ seconds (2, 4, 8, 16 s) |
| Timeout per attempt | 10 s |
| Failure alert | `ALERT_WEBHOOK_URL` env var (Slack/Discord compatible) |
| SSRF protection | Internal IPs and metadata endpoints blocked |

After 5 failed attempts the delivery is marked **failed** and no further retries occur. Use the `ALERT_WEBHOOK_URL` env var to receive a notification when this happens.

## Slack integration example

```json
{
  "url": "https://hooks.slack.com/services/T.../B.../...",
  "label": "Slack #dev-pipeline",
  "events": ["pipeline.end", "hitl.pending"]
}
```

The Slack incoming webhook will receive a JSON body. You can parse it in a Slack workflow or use a middleware to format it as a Slack message block.

## Example: filter only failed runs

Use a lightweight serverless function to filter and reformat:

```python
# Vercel / AWS Lambda handler
def handler(event, context):
    body = json.loads(event["body"])
    if body.get("event_type") == "pipeline.end" and not body.get("success"):
        # Send Slack alert only on failure
        slack_notify(f"Pipeline {body['run_id']} FAILED — cost ${body.get('cost_usd', 0):.4f}")
    return {"statusCode": 200}
```
