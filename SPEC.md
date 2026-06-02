# SPEC.md

```md
# SPEC.md — PolyBot Technical Specification

Version: 2.0
Status: Active

---

# 1. System Overview

PolyBot is a modular algorithmic trading platform designed for Polymarket crypto prediction markets.

Supported assets:
- BTC
- ETH

Supported windows:
- M5
- M15

Supported modes:
- paper
- canary
- production

The platform focuses on:
- resilient execution
- measurable strategies
- statistical validation
- operational safety
- continuous research iteration

---

# 2. High-Level Architecture

Main flow:

Market Data
    ↓
Strategy Engine
    ↓
Risk Engine
    ↓
Execution Engine
    ↓
Persistence + Metrics + Notifications

Supporting systems:
- observability
- analytics
- dashboard
- telegram control
- backtesting
- chaos validation

---

# 3. Core Components

---

## 3.1 Market Data Layer

Responsibilities:
- consume WebSocket market data
- maintain latest tick state
- provide REST fallback
- detect stale connections
- expose degradation state

Features:
- WS → REST graceful degradation
- stale detection
- reconnect logic
- source tracking
- latency monitoring

Primary outputs:
- MarketTick
- orderbook snapshots
- spread metrics
- liquidity metrics

---

## 3.2 Strategy Engine

Responsibilities:
- evaluate market conditions
- generate signals
- apply filters
- assign confidence scores

Current strategies:
- BuyAboveThreshold (BAT)
- MeanReversion (MR)

Future planned:
- regime-aware strategies
- event-driven strategies
- liquidity-aware strategies
- portfolio-level orchestration

---

# 4. Current Strategies

---

## 4.1 Buy Above Threshold (BAT)

Purpose:
Capture directional continuation when probability exceeds configured threshold.

Inputs:
- yes_price
- consecutive ticks
- spread
- liquidity
- MTF confirmation

Entry Conditions:
- yes_price >= threshold
- required_ticks confirmed
- spread acceptable
- liquidity acceptable

Exit Conditions:
- target reached
- stop loss reached
- timeout
- market expiry proximity

Filters:
- spread filter
- liquidity filter
- time filter
- multi-timeframe confirmation

Outputs:
- BUY signal
- HOLD signal
- confidence score

---

## 4.2 Mean Reversion (MR)

Purpose:
Exploit short-term probability deviations from local mean.

Inputs:
- rolling SMA
- rolling standard deviation
- z-score

Entry Conditions:
- z_score < entry threshold

Exit Conditions:
- z_score mean reversion
- stop loss
- timeout

Metrics:
- MR_ZSCORE
- MR_ENTRY_CONFIDENCE

---

# 5. Risk Engine

Purpose:
Protect capital and enforce portfolio constraints.

Architecture:
Rule-based sequential evaluation.

Current rules:
- Kelly sizing
- max exposure
- drawdown protection
- minimum balance
- max positions
- hedge rules

Output:
RiskDecision:
- ALLOW
- DENY
- REDUCE_SIZE

---

## 5.1 Kelly Sizing

Inputs:
- confidence
- market price
- volatility
- target

Constraints:
- floor position size
- max position cap
- never exceed requested amount

Purpose:
Dynamic capital allocation based on estimated edge.

---

## 5.2 Drawdown Protection

Purpose:
Prevent catastrophic capital loss.

Behavior:
- deny entries beyond configured DD
- enforce safety shutdowns

---

## 5.3 Exposure Limits

Purpose:
Avoid concentration risk.

Constraints:
- per-market exposure
- total exposure
- correlated exposure (future)

---

# 6. Execution Engine

Responsibilities:
- place orders
- manage retries
- enforce idempotency
- track execution results
- prevent duplicate execution

Modes:
- paper execution
- real execution

---

## 6.1 Real Execution

Current implementation:
- py-clob-client-v2
- EIP-712 signing
- async wrappers
- retry logic
- circuit breaker integration

Safety Features:
- deterministic idempotency
- retry limits
- execution guardrails
- audit logging

---

## 6.2 Circuit Breaker

Behavior:
- opens after repeated failures
- blocks execution during instability
- half-open recovery mode

Purpose:
Prevent cascading API failures.

---

# 7. Persistence Layer

Primary storage:
- PostgreSQL

Cache/storage acceleration:
- Redis

Persisted entities:
- markets
- positions
- orders
- audit logs
- bot settings

---

# 8. API Layer

Framework:
- FastAPI

Responsibilities:
- health endpoints
- metrics exposure
- dashboard APIs
- positions/orders APIs

Current capabilities:
- filtering
- monitoring
- status reporting
- graceful degradation handling

---

# 9. Telegram Interface

Purpose:
Operational control and monitoring.

Capabilities:
- start/stop bot
- mode switching
- settings management
- position visibility
- health monitoring

Security:
- PIN validation
- audit logging

---

# 10. Dashboard

Frontend:
- React + Vite + TypeScript

Capabilities:
- balance monitoring
- equity curves
- positions
- orders
- market overview
- health metrics

Future additions:
- regime analysis
- slippage analytics
- execution attribution
- risk heatmaps

---

# 11. Observability

Current stack:
- Prometheus
- Grafana
- OpenTelemetry

Monitored areas:
- execution latency
- API health
- WS health
- circuit breaker state
- trading performance
- strategy signals
- risk metrics

---

# 12. Testing Architecture

Current test categories:
- unit
- integration
- property-based
- performance
- security
- chaos

Requirements:
- deterministic tests
- isolated side effects
- async-safe tests
- reproducible failures

---

# 13. Backtesting System

Purpose:
Evaluate strategy behavior before deployment.

Current capabilities:
- synthetic datasets
- parameter sweeps
- metric evaluation

Current limitations:
- limited real historical data
- simplified execution assumptions

Future requirements:
- replay engine
- realistic fills
- slippage modeling
- queue modeling
- liquidity simulation

---

# 14. Research Pipeline

Planned workflow:
1. collect real data
2. generate features
3. formulate hypothesis
4. backtest
5. validate statistically
6. paper trade
7. canary deploy
8. scale gradually

Research principles:
- avoid overfitting
- validate across regimes
- prioritize robustness
- preserve reproducibility

---

# 15. Market Data Strategy

Current reality:
Polymarket historical data is limited.

Current solution:
- live WebSocket recording
- local dataset construction

Future requirements:
- replayable datasets
- feature store
- regime labeling
- event tagging

---

# 16. Execution Assumptions

Current assumptions:
- immediate fills (partially simplified)
- stable liquidity
- moderate slippage

Known gaps:
- queue priority
- partial fills
- liquidity collapse
- dynamic spread expansion

Future execution engine must model:
- fill probability
- queue position
- spread impact
- cancel latency

---

# 17. Quantitative Goals

Current target metrics:
- Sharpe > 1.0
- Profit Factor > 1.3
- Drawdown < 15%
- Positive expectancy
- Stable performance across regimes

Important:
Synthetic profitability alone is NOT sufficient.

Real-world validation required:
- paper trading
- canary execution
- real slippage measurement

---

# 18. Deployment Architecture

Infrastructure:
- Docker
- Kubernetes
- GitHub Actions CI/CD

Environments:
- staging
- canary
- production

Security:
- Vault integration
- secret isolation
- hardened containers

---

# 19. Failure Philosophy

The system must fail safely.

Preferred outcomes:
HOLD > reduced risk > full stop > unsafe execution

Never prioritize:
- uptime over safety
- execution over validation
- profitability over survivability

---

# 20. Future Direction

Planned evolution:

V1:
- resilient trading bot

V2:
- quantitative research platform

V3:
- multi-strategy execution system

V4:
- regime-aware probabilistic engine

V5:
- event-driven prediction market platform

---

# 21. Current Bottlenecks

Primary bottlenecks are now:
- real data availability
- edge validation
- execution realism
- market microstructure understanding

NOT:
- infrastructure
- CI/CD
- observability
- deployment maturity

---

# 22. Definition of Success

The system is considered successful if it achieves:
- statistically robust profitability
- controlled drawdowns
- operational stability
- execution reliability
- long-term survivability

without relying on:
- unrealistic assumptions
- synthetic-only optimization
- unstable overfitted parameters
```