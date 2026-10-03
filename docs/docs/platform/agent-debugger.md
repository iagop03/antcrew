# Agent Debugger

The Agent Debugger is a time-travel interface for run events built into the run detail page. It lets you step forward and backward through every event in a pipeline run, watching costs, tokens, and agent state accumulate event by event.

## Opening the debugger

1. Navigate to a run detail page: **Runs → [run] → Debug tab**
2. Click **Debug** in the tab bar (after Overview, Agents, Failovers)
3. Event data loads automatically on first open (reuses already-loaded events if the Agents tab was already visited)

## Timeline scrubber

The top of the Debug panel shows a dot for each event in the run, color-coded by type:

| Color | Event type |
|-------|-----------|
| Green | `pipeline.start` |
| Indigo | `agent.*` (agent.start, agent.end) |
| Teal | `pipeline.end` |
| Yellow | `llm.fallback` |

Drag the range slider or click a dot to jump to any event. The running totals and event detail card update instantly.

## Playback controls

| Control | Action |
|---------|--------|
| **‹** | Step to previous event |
| **›** | Step to next event |
| **▶ / ⏸** | Play / pause auto-advance |
| Speed | `0.5×` / `1×` / `2×` / `5×` — controls auto-advance interval |

During playback, the debugger advances one event at a time at the selected speed and stops automatically at the last event.

## Running totals

Three counters update at each step to show cumulative state up to the current event:

- **Cost** — sum of `cost_usd` from all `agent.end` events up to this step; at the final step, uses the authoritative `pipeline.end` cost
- **Tokens in** — sum of `tokens_in` from `agent.end` payloads
- **Tokens out** — sum of `tokens_out` from `agent.end` payloads

This lets you see exactly which agent or step in the pipeline drove the majority of cost or token usage.

## Event detail card

Below the running totals, the current event's full payload is shown:

- Event type badge (color-coded)
- Timestamp (ISO 8601)
- JSON payload — expandable inline, with agent name, model, cost, and tokens highlighted for `agent.end` events

## Data source

The debugger reads from `GET /runs/{run_id}/events`, the same endpoint used by the Agents tab. Events are sorted by `timestamp` ascending. No additional backend calls are made if events were already loaded when the Agents tab was visited.

## Use cases

**Cost attribution** — step through an expensive run to find which agent call drove the cost spike.

**Failover investigation** — spot `llm.fallback` events in the timeline and see the payload (original model, fallback model, reason) at that exact step.

**Agent sequencing** — verify that agents fired in the expected order and that each completed before the next started.

**Debugging missing output** — step to the last `agent.end` before a failure to inspect the payload that was returned.

## TraceLog vs Agent Debugger

| | Agent Debugger | TraceLog |
|---|---|---|
| Interface | Interactive UI in run detail page | NDJSON stream |
| Access | `/runs/{id}` → Debug tab | `GET /runs/{id}/trace.ndjson` |
| Use case | Manual investigation | Automated export, SIEM ingestion |
| Events | All events, sorted by time | Same events + run metadata header |

For programmatic access to the same event data:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.antcrew.com/runs/{run_id}/events
```

Or stream as NDJSON with the full TraceLog format:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://api.antcrew.com/runs/{run_id}/trace.ndjson
```
