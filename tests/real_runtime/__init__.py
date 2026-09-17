"""Real-runtime production execution gate.

These tests forbid DEVOS_ORCH_FAKE_RUNTIME and exercise the actual spine:
  request → Nuha/plan/delegation → run_node_on_agent_runtime → CodingLoop/AgentRuntime
  → UCIP → provider → tools → workspace → evidence → mission acceptance.

Deterministic fake-runtime unit tests live under tests/test_*.py and must not
be confused with this suite.
"""
