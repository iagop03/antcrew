"""DBSchemaAgent — designs relational or document database schemas from requirements."""
from __future__ import annotations

from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import DBColumn, DBTable, DatabaseSchema, Ticket, coerce_list
from antcrew.core.state import TeamState

_SYSTEM = """\
You are a database architect. Design a complete database schema from the requirements.

Respond ONLY with a valid JSON object:
{
  "title": "<schema title>",
  "db_type": "<postgresql|mysql|mongodb|sqlite>",
  "tables": [
    {
      "name": "<table_name>",
      "description": "<what this table stores>",
      "columns": [
        {
          "name": "<column_name>",
          "type": "<VARCHAR(255)|INTEGER|TIMESTAMP|BOOLEAN|TEXT|JSONB|UUID|etc.>",
          "nullable": <true|false>,
          "primary_key": <true|false>,
          "foreign_key": "<table.column or null>",
          "unique": <true|false>,
          "description": "<brief note>"
        }
      ],
      "indexes": ["<index on column_name>"]
    }
  ],
  "relationships": ["<TableA.col → TableB.col: relationship type>"],
  "migration_notes": "<notes on migration strategy>",
  "rationale": "<design decisions>"
}

Always include id (UUID PK), created_at, and updated_at columns.
"""


class DBSchemaAgent(BaseAgent):
    name = "db_schema"
    role_description = "Designs database schemas: tables, columns, indexes, and entity relationships."
    consumes: list[str] = ["request", "prd", "tickets"]
    produces: list[str] = ["database_schema"]

    def run(self, state: TeamState) -> dict:
        request = state.get("request", "")
        prd = state.get("prd")
        tickets = coerce_list(state, "tickets", Ticket)

        parts = [request]
        if prd:
            raw = prd.model_dump() if hasattr(prd, "model_dump") else prd
            parts.append(f"PRD: {raw.get('summary', '')}")
            if raw.get("functional_requirements"):
                parts.append("Requirements:\n" + "\n".join(f"- {r}" for r in raw["functional_requirements"][:10]))
        if tickets:
            parts.append(f"Features: {', '.join(t.title for t in tickets[:10])}")

        data: dict = self.system_parsed(_SYSTEM, "\n\n".join(parts), dict)

        try:
            tables = []
            for t in (data.get("tables") or []):
                columns = [DBColumn(**c) for c in (t.get("columns") or [])]
                tables.append(DBTable(
                    name=t["name"],
                    description=t.get("description", ""),
                    columns=columns,
                    indexes=t.get("indexes") or [],
                ))
            schema = DatabaseSchema(
                title=data.get("title", "Database Schema"),
                db_type=data.get("db_type", "postgresql"),
                tables=tables,
                relationships=data.get("relationships") or [],
                migration_notes=data.get("migration_notes", ""),
                rationale=data.get("rationale"),
            )
        except Exception:
            schema = DatabaseSchema(title="Database Schema")

        return {
            "database_schema": schema,
            "current_agent": self.name,
            "messages": [{
                "role": "assistant",
                "content": (
                    f"[DBSchema] {schema.title} ({schema.db_type}): "
                    f"{len(schema.tables)} table(s), "
                    f"{len(schema.relationships)} relationship(s)."
                ),
            }],
        }
