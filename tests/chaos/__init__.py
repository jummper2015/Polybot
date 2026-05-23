# tests/chaos/__init__.py
"""
Chaos testing for the Polybot algorithmic trading system.

Tests validate that the system maintains stability under failure conditions:
  - Steady-state hypotheses: invariants that must ALWAYS hold
  - Chaos scenarios: simulated failure modes the system must survive
  - Formal Chaos Toolkit experiments for CI/CD pipeline integration

Usage:
    # Run all chaos tests (Python)
    python -m pytest tests/chaos/ -v

    # Run formal Chaos Toolkit experiments
    chaos run tests/chaos/experiments/ws_disconnection.json
"""

__all__ = []
