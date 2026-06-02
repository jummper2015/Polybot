# CLAUDE.md

```md
# CLAUDE.md — PolyBot AI Engineering Context

## Project Identity

PolyBot is a production-oriented algorithmic trading system for Polymarket focused on crypto prediction markets (BTC/ETH).

Current supported windows:
- M5
- M15

Current modes:
- paper
- canary
- production

Primary goals:
1. Long-term profitability
2. Operational robustness
3. Statistical validity
4. Safe execution
5. Continuous research iteration

Priority order:
robustness > correctness > observability > profitability > optimization

---

# Core Philosophy

The system must behave like a resilient quantitative platform, not a retail trading bot.

Key principles:
- Avoid overfitting
- Prefer statistical validation over intuition
- All strategies must be measurable
- Execution quality matters as much as signal quality
- Graceful degradation is mandatory
- Every failure must be observable
- Production safety is non-negotiable

Never optimize exclusively for:
- win rate
- Sharpe on synthetic data
- backtest-only profitability

Always validate:
- real fills
- slippage
- liquidity
- latency
- regime robustness

---

# Current Architecture

Main layers:

1. Domain
- entities
- enums
- exceptions
- business rules

2. Strategies
- BAT strategy
- Mean Reversion strategy
- filters
- signal generation

3. Risk Engine
- Kelly sizing
- exposure limits
- drawdown protection
- hedge rules
- max positions

4. Execution
- paper execution
- real execution
- retry logic
- circuit breaker
- idempotency

5. Infrastructure
- PostgreSQL
- Redis
- WebSocket market data
- REST fallback
- Prometheus
- OpenTelemetry

6. Interfaces
- FastAPI
- Telegram
- React dashboard

7. Research
- backtesting
- optimization
- validation scripts
- historical datasets

---

# Trading Philosophy

Strategies are hypotheses, not truths.

Every strategy must answer:
- where does edge come from?
- under which market regimes?
- what invalidates the edge?
- what execution assumptions are required?

The system must eventually evolve toward:
- regime-aware trading
- probabilistic models
- execution-aware alpha
- portfolio-level optimization

---

# Development Rules

## General Rules

- Prefer incremental changes
- Never rewrite large stable modules unnecessarily
- Preserve backward compatibility when possible
- Keep async compatibility
- Avoid hidden state mutations
- Prefer composition over inheritance
- Minimize coupling

## File Modification Rules

Only modify files directly related to the task.

Avoid:
- unrelated formatting changes
- mass import rewrites
- unnecessary renaming
- large refactors without RFC

## Dependency Rules

Before adding dependencies:
- justify necessity
- check async compatibility
- verify maintenance quality
- evaluate security impact
- evaluate performance impact

Avoid heavy dependencies unless necessary.

---

# Testing Rules

All meaningful changes require tests.

## Required Testing Types

### Unit Tests
Required for:
- business logic
- utilities
- strategies
- risk rules

### Integration Tests
Required for:
- DB interactions
- Redis interactions
- execution handlers
- API routers

### Property Tests
Required for:
- invariants
- risk boundaries
- order lifecycle
- sizing constraints

### Chaos / Resilience Tests
Required for:
- network degradation
- retry behavior
- failover logic
- circuit breaker behavior

---

# Quantitative Research Rules

Do NOT assume profitability from synthetic data.

All strategy improvements must eventually pass:
- walk-forward validation
- out-of-sample validation
- paper trading validation
- real execution validation

Always consider:
- slippage
- latency
- liquidity
- spread widening
- partial fills

Avoid parameter over-optimization.

---

# Risk Rules

Hard requirements:
- max drawdown protection
- exposure caps
- minimum balance protection
- kill switches
- circuit breakers
- execution retry limits

The system must fail safely.

If uncertain:
- reduce risk
- pause execution
- prefer HOLD over ENTRY

---

# Observability Rules

Every critical workflow must expose:
- metrics
- traces
- logs
- error context

Critical systems:
- execution
- market data
- risk engine
- strategy evaluation
- API failures
- degradation modes

Never introduce silent failures.

---

# Performance Rules

Prefer:
- async I/O
- bounded concurrency
- streaming over full-memory loads
- orjson
- efficient serialization

Avoid:
- blocking operations inside async code
- unbounded queues
- unnecessary DB roundtrips
- repeated heavy computations

---

# Token Optimization Rules

IMPORTANT FOR AI-ASSISTED DEVELOPMENT

Always:
- prefer minimal diffs
- avoid rewriting entire files
- avoid repeating context
- modify only relevant modules
- preserve existing architecture
- keep responses concise when implementing

Before implementation:
1. analyze
2. propose plan
3. implement incrementally

Avoid generating unnecessary boilerplate.

---

# Production Safety Rules

Production changes must:
- preserve existing tests
- preserve observability
- preserve risk controls
- preserve execution safety

Never:
- bypass risk engine
- disable safeguards
- weaken idempotency
- remove retry protections
- reduce logging visibility

---

# Current Strategic Priorities

1. Real market data collection
2. Replay engine
3. Execution realism
4. Post-trade analytics
5. Regime detection
6. Strategy validation
7. Liquidity-aware execution
8. Multi-strategy orchestration

---

# Long-Term Vision

PolyBot is evolving toward:
- quantitative research platform
- prediction-market execution engine
- multi-strategy trading framework
- event-driven probabilistic system

The next bottleneck is NOT engineering.

The next bottleneck is:
- statistical edge discovery
- execution quality
- market microstructure understanding

---

# Instructions For AI Assistants

When working on tasks:
- preserve architecture consistency
- prefer small safe changes
- include tests where appropriate
- explain risks if detected
- avoid speculative rewrites
- keep implementation production-oriented

If requirements are ambiguous:
- ask for clarification
OR
- implement smallest safe interpretation
```