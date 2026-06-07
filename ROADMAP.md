# ROADMAP.md

```md
> **⚠️ HISTÓRICO — Este documento está congelado.**  
> El roadmap activo y actualizado se encuentra en [`WORKFLOW.md`](./WORKFLOW.md)  
> y el seguimiento detallado en [`RECORRIDO.txt`](./RECORRIDO.txt).  
> La numeración de fases en este documento (6-11) corresponde a la  
> antigua y ha sido reemplazada por Fases 8-13 en el resto del proyecto.

---

# ROADMAP.md — PolyBot Strategic Roadmap

Version: 2.0
Status: Active

---

# Mission

Transform PolyBot from a robust trading bot into a statistically validated quantitative trading platform specialized in prediction markets.

Primary objective:
Generate long-term risk-adjusted profitability with production-grade operational reliability.

---

# Guiding Principles

- Robustness over aggressiveness
- Statistical validation over intuition
- Real-world execution over theoretical backtests
- Incremental deployment over rapid scaling
- Survivability over short-term profit

---

# Current State

Completed:
- production infrastructure
- CI/CD
- observability
- chaos testing
- resilient execution
- strategy framework
- risk engine
- dashboard
- Telegram controls
- initial backtesting system

Current bottleneck:
Real statistical edge validation.

---

# Strategic Phases

---

# PHASE 6 — DATA & RESEARCH FOUNDATION

Status: ACTIVE

Goal:
Build the data and research infrastructure required for serious quantitative development.

Priority: CRITICAL

---

## P6.1 — Continuous Real Market Recording

Status: TODO

Objectives:
- collect live WS data continuously
- build proprietary datasets
- store replayable market streams

Requirements:
- parquet storage
- compressed datasets
- deterministic timestamps
- orderbook snapshots
- spread tracking
- liquidity metrics

Success Criteria:
- 30+ days uninterrupted collection
- replayable datasets
- low data loss rate

---

## P6.2 — Replay Engine

Status: TODO

Objectives:
- deterministic market replay
- historical simulation
- accelerated backtesting

Requirements:
- async-safe
- streaming architecture
- configurable replay speed
- reproducible runs

Success Criteria:
- replay accuracy validated
- stable performance on large datasets

---

## P6.3 — Feature Store

Status: TODO

Objectives:
- centralized feature generation
- reusable research features
- regime labeling support

Features:
- spread percentile
- imbalance
- realized volatility
- liquidity depth
- momentum decay
- event proximity

Success Criteria:
- reproducible feature pipelines
- offline + online compatibility

---

## P6.4 — Regime Labeling

Status: TODO

Objectives:
- classify market states
- support adaptive strategies

Regimes:
- trend
- chop
- panic
- illiquid
- event-driven

Success Criteria:
- stable classification quality
- useful predictive separation

---

# PHASE 7 — EXECUTION REALISM

Status: PLANNED

Goal:
Reduce simulation-reality gap.

Priority: CRITICAL

---

## P7.1 — Realistic Fill Simulation

Status: TODO

Objectives:
- simulate partial fills
- model execution uncertainty

Requirements:
- probabilistic fills
- spread crossing logic
- liquidity constraints

Success Criteria:
- backtest execution approximates production

---

## P7.2 — Slippage Engine

Status: TODO

Objectives:
- estimate execution cost dynamically

Inputs:
- spread
- liquidity
- volatility
- order size

Success Criteria:
- realistic slippage distributions

---

## P7.3 — Queue Position Modeling

Status: TODO

Objectives:
- estimate fill probability
- improve passive execution

Success Criteria:
- measurable execution improvement

---

## P7.4 — Smart Order Routing

Status: TODO

Objectives:
- adaptive maker/taker behavior
- execution optimization

Success Criteria:
- lower slippage
- higher realized edge retention

---

# PHASE 8 — QUANTITATIVE VALIDATION

Status: PLANNED

Goal:
Develop statistically defensible alpha.

Priority: VERY HIGH

---

## P8.1 — Walk-Forward Validation

Status: TODO

Objectives:
- eliminate static overfitting
- validate temporal robustness

Success Criteria:
- stable out-of-sample performance

---

## P8.2 — Monte Carlo Simulation

Status: TODO

Objectives:
- evaluate robustness under randomness

Metrics:
- drawdown distributions
- tail risk
- equity stability

Success Criteria:
- acceptable worst-case scenarios

---

## P8.3 — Confidence Calibration

Status: TODO

Objectives:
- align confidence with actual probability

Metrics:
- Brier score
- reliability curves

Success Criteria:
- calibrated probability outputs

---

## P8.4 — Post-Trade Analytics Engine

Status: TODO

Objectives:
- detailed performance attribution

Metrics:
- expectancy
- MAE/MFE
- slippage attribution
- regime attribution

Success Criteria:
- explainable profitability sources

---

# PHASE 9 — ADVANCED STRATEGIES

Status: PLANNED

Goal:
Move beyond simple threshold logic.

Priority: HIGH

---

## P9.1 — Regime-Aware Strategy Switching

Status: TODO

Objectives:
- activate strategies conditionally

Success Criteria:
- lower drawdowns
- higher consistency

---

## P9.2 — Ensemble Signal Engine

Status: TODO

Objectives:
- combine multiple alpha sources

Components:
- momentum
- mean reversion
- liquidity
- event signals

Success Criteria:
- improved risk-adjusted returns

---

## P9.3 — Liquidity-Aware Trading

Status: TODO

Objectives:
- adapt behavior to market depth

Success Criteria:
- lower execution degradation

---

## P9.4 — Event-Driven Trading

Status: TODO

Objectives:
- react to external information

Potential Inputs:
- news
- economic calendar
- social sentiment

Success Criteria:
- measurable event capture edge

---

# PHASE 10 — PORTFOLIO & SCALING

Status: FUTURE

Goal:
Scale safely across strategies and capital sizes.

Priority: HIGH

---

## P10.1 — Portfolio Risk Engine

Status: TODO

Objectives:
- cross-strategy exposure control
- correlation management

Success Criteria:
- controlled aggregate risk

---

## P10.2 — Dynamic Capital Allocation

Status: TODO

Objectives:
- adaptive sizing across strategies

Success Criteria:
- improved capital efficiency

---

## P10.3 — Multi-Market Expansion

Status: FUTURE

Potential:
- additional prediction markets
- cross-market arbitrage
- volatility products

---

# PHASE 11 — AI / ML RESEARCH

Status: EXPERIMENTAL

Goal:
Explore machine learning cautiously.

Priority: LOW

Important:
ML must only be introduced after:
- strong data foundation
- reliable feature engineering
- validated execution realism

---

## P11.1 — Gradient Boosted Models

Status: FUTURE

Potential Uses:
- signal ranking
- probability estimation
- regime classification

---

## P11.2 — Meta-Labeling

Status: FUTURE

Objectives:
- improve signal filtering

---

## P11.3 — Online Learning

Status: FUTURE

Objectives:
- adaptive model updates

Constraints:
- strong safeguards required

---

# Operational Milestones

---

## Milestone A — Research Ready

Requirements:
- replay engine
- feature store
- real datasets
- walk-forward testing

Target:
Reliable research environment.

---

## Milestone B — Production Alpha Validation

Requirements:
- 60+ days paper trading
- positive expectancy
- acceptable drawdowns
- stable execution quality

Target:
Validated edge candidate.

---

## Milestone C — Real Capital Stability

Requirements:
- successful canary deployment
- low slippage drift
- stable operational metrics

Target:
Small-scale live profitability.

---

## Milestone D — Scalable Quant Platform

Requirements:
- multi-strategy orchestration
- portfolio optimization
- execution alpha

Target:
Institutional-grade architecture.

---

# Current Top Priorities

1. Real market recording
2. Replay engine
3. Post-trade analytics
4. Realistic execution simulation
5. Walk-forward validation
6. Regime detection

---

# Current Anti-Goals

Avoid:
- overfitting synthetic data
- adding ML prematurely
- excessive strategy proliferation
- scaling capital too quickly
- optimizing vanity metrics

---

# Success Definition

PolyBot succeeds if:
- profitability survives real execution
- drawdowns remain controlled
- operational reliability stays high
- strategies remain statistically defensible
- the system adapts across market regimes

without relying on:
- unrealistic fills
- synthetic-only optimization
- unstable parameter tuning
- hidden operational risk
```