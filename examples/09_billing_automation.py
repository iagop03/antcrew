"""
09 — Real-world example: billing automation (Font Jardineria)

Font Jardineria is a small landscaping company (5 staff) that uses antcrew
to turn WhatsApp job summaries into polished PDF invoices.  Their ops manager
types a free-form job note; the DevTeam pipeline produces:

  • A structured PRD with billing line-items
  • Implementation tickets for the billing micro-service
  • A Python invoice generator script they can run directly

This example shows the same flow with SimulatedLLM so you can run it locally
without any API key.  Swap SimulatedLLM for a real model to use in production.

Run:
    python examples/09_billing_automation.py
"""
from antcrew import DevTeam
from antcrew.models import SimulatedLLM

JOB_NOTE = """
Client: Casa Montserrat, Carrer del Roure 14
Date:   12 Jul 2025
Work:   Full garden maintenance — lawn mow + edge (120 m²), hedge trim (30 lin m),
        weeding raised beds x3, replant 4 lavender + 2 rosemary.
Hours:  3.5 h labour (2 workers).
Materials:  6 lavender (€4.50 ea), 2 rosemary (€3.20 ea), compost bag (€8.90).
Notes:  Client asked for bi-weekly maintenance quote.
"""

GOAL = f"""
Build a billing automation tool for a landscaping company.

Job summary:
{JOB_NOTE}

Requirements:
1. Parse the job note and extract: client name, date, labour hours/workers,
   materials with unit prices, and any follow-up actions.
2. Calculate totals: labour at €28/h/worker, materials at cost + 20 % margin,
   plus 21 % IVA (Spanish VAT).
3. Generate an invoice in PDF-ready format (structured data + HTML template).
4. Save a JSON record to ./invoices/ for accounting export.
5. Output a friendly quote for the bi-weekly maintenance request.
"""

team = DevTeam(model=SimulatedLLM())
state = team.run(GOAL)

# ── PRD ───────────────────────────────────────────────────────────────────────
if state.get("prd"):
    prd = state["prd"]
    print(f"\n=== PRD: {prd.title} ===")
    print(prd.summary)

# ── Tickets ───────────────────────────────────────────────────────────────────
if state.get("tickets"):
    print(f"\n=== Tickets ({len(state['tickets'])}) ===")
    for t in state["tickets"]:
        print(f"  [{t.priority.value:8}] {t.id}: {t.title}")

# ── Code artifacts ─────────────────────────────────────────────────────────────
if state.get("code_artifacts"):
    print(f"\n=== Code files ({len(state['code_artifacts'])}) ===")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}  ({a.language})")
        print(f"  # {a.description}")

print("""
─────────────────────────────────────────────────────────────────────────────
To generate a real invoice, replace SimulatedLLM() with your preferred model:

    from antcrew import DevTeam
    team = DevTeam(model="claude")          # Anthropic (ANTHROPIC_API_KEY)
    # team = DevTeam(model="gpt-4o")        # OpenAI   (OPENAI_API_KEY)
    # team = DevTeam(model="gemini:gemini-2.0-flash") # Google (GOOGLE_API_KEY)
─────────────────────────────────────────────────────────────────────────────
""")
