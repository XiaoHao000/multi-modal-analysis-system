"""Specialist Agents — each agent autonomously makes decisions within its domain using tool-calling.

Architecture change: from deterministic pipeline → Supervisor + Specialist multi-agent.

Agent types:
  - Supervisor Agent: dynamic routing, decides which specialists to invoke and in what order
  - SQL ReAct Agent: think → get_schema → generate_sql → execute → observe → revise loop
  - Analysis Agent: multi-step drill-down with cross-reference capabilities
  - Report Agent: intelligent chart selection + suggestion generation
"""
