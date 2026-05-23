# tests/chaos/ — Chaos Testing for Polybot

Chaos engineering tests verify the Polybot trading system maintains stability under failure conditions.

## Structure

```
tests/chaos/
├── __init__.py                          # Package init
├── README.md                            # This file
├── test_steady_state_hypotheses.py      # 3 invariants that MUST always hold
├── test_chaos_scenarios.py              # 5 failure scenario simulations
└── experiments/                         # Formal Chaos Toolkit experiments
    ├── ws_disconnection.json            # S1: WS disconnection
    ├── redis_failure.json               # S2: Redis failure
    ├── db_pool_exhaustion.json          # S3: DB pool exhaustion
    ├── api_packet_loss.json             # S4: API 50% packet loss
    └── high_latency.json                # S5: High latency 500ms+
```

## Quick Start

### Python Chaos Tests (pytest)

```bash
# Run all chaos tests
python -m pytest tests/chaos/ -v

# Run steady-state hypotheses only
python -m pytest tests/chaos/test_steady_state_hypotheses.py -v

# Run chaos scenarios only
python -m pytest tests/chaos/test_chaos_scenarios.py -v
```

### Formal Chaos Toolkit Experiments

```bash
# Install Chaos Toolkit
pip install chaostoolkit

# Run a single experiment
chaos run tests/chaos/experiments/ws_disconnection.json

# Run all experiments
for exp in tests/chaos/experiments/*.json; do
    echo "=== Running: $exp ==="
    chaos run "$exp"
done
```

## Steady-State Hypotheses

These invariants define system "health" — if any fail, the bot should halt.

| # | Hypothesis | Verification |
|---|-----------|-------------|
| **H1** | Bot NEVER sends duplicate orders | SHA256 idempotency key is deterministic; same signal + minute → same key |
| **H2** | Balance NEVER drops below min_balance | MinBalanceRule + DrawdownRule + KellySizingRule always evaluated before execution |
| **H3** | RiskEngine ALWAYS evaluated before executing | _evaluate_risk_and_execute() calls risk.evaluate() BEFORE execute_entry() |

## Chaos Scenarios

| # | Scenario | What We Inject | Expected Behavior |
|---|---------|---------------|-------------------|
| **S1** | WS Disconnection | Block WS for 120s | Graceful degradation to REST polling; recovery when WS restores |
| **S2** | Redis Failure | Stop Redis for 60s | Fallback to DB queries; balance state preserved in memory |
| **S3** | DB Pool Exhaustion | Hold all 5 connections for 60s | Timeout after 30s (pool_timeout); no data corruption |
| **S4** | API Packet Loss | Drop 50% of packets for 120s | Circuit breaker opens after 5 failures; retries exhausted gracefully |
| **S5** | High Latency | Add 500ms to all calls for 90s | Timeouts respected (10s); no hung operations; guardrails still active |

## Guardrails Verified

Each chaos scenario also implicitly verifies these safety guardrails:

- **Hardcoded limits**: MAX_ORDER_AMOUNT=500 USDC, MIN_ORDER_AMOUNT=1 USDC
- **Circuit breaker**: opens at 5 failures / 60s window, recovery after 60s
- **Idempotency**: SHA256 keys prevent duplicate orders during retries
- **Audit log**: all real order attempts logged (success + failure)
- **SecurityGuard**: checks run BEFORE any API call (synchronous, not affected by latency)

## Adding New Chaos Scenarios

1. Create a new test file in `tests/chaos/` following the naming convention
2. Add a corresponding experiment JSON in `tests/chaos/experiments/`
3. Both should reference the same scenario name and tag

### Python Test Template

```python
class TestNewScenario:
    """Description of what this scenario tests."""

    def test_expected_behavior(self):
        # 1. Setup: configure the failure
        # 2. Inject: trigger the failure
        # 3. Observe: verify system response
        # 4. Recover: verify system returns to normal
        pass
```

### Chaos Toolkit Experiment Template

```json
{
  "version": "1.0.0",
  "title": "Scenario Title",
  "description": "What this experiment tests",
  "steady-state-hypothesis": {
    "title": "Expected outcome",
    "probes": [
      {
        "name": "probe-name",
        "type": "probe",
        "provider": {
          "type": "python",
          "module": "tests.chaos.probes",
          "func": "your_probe_function"
        },
        "tolerance": { "type": "jsonpath", "path": "$.result", "expect": "expected_value" }
      }
    ]
  },
  "method": [
    {
      "type": "action",
      "name": "inject-failure",
      "provider": {
        "type": "python",
        "module": "tests.chaos.actions",
        "func": "your_action_function"
      }
    }
  ]
}
```

## Dependencies

- Python 3.12+
- pytest ≥ 7.0
- chaostoolkit ≥ 1.19.0 (for JSON experiments)
- The full Polybot dependency stack (requirements.txt)
