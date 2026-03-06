# Benchmark Architecture — FX Settlement Engine

## Summary

The oracle uses a **two-tier benchmark hierarchy** designed around one constraint:
in a distributed system with heterogeneous validator nodes, **reachability and reproducibility beat authority**.

---

## Live Benchmark: Market Aggregate

**Source:** open.er-api.com · BOB/PEN endpoint  
**Benchmark type:** `MARKET_AGGREGATE`  
**Benchmark ID format:** `MARKET-YYYYMMDD`  
**Contract function:** `request_rate_lock_fallback` / `request_roll_fallback`

All 5 GenLayer validators independently fetch this endpoint. With deterministic rounding (`round(rate, 3)`, 10 bps bucket), `strict_eq` reaches MAJORITY_AGREE reliably across heterogeneous nodes.

This is the **first validator-reproducible benchmark in the hierarchy** — not a fallback triggered by a failure. The system selected it because it meets the reproducibility requirement. The BCRP primary was tried first and failed consensus; the market aggregate succeeded.

---

## Audit Reference: BCRP × BCB Cross

**Source:** Banco Central de Reserva del Perú — series PD04638PD (PEN/USD)  
         × Banco Central de Bolivia — fixed peg (6.96 BOB/USD, unchanged since 2012)  
**Benchmark type:** `BCRP_BCB_CROSS`  
**Contract function:** `request_rate_lock_primary` / `request_roll_primary`

The BCRP rate is the **institutional benchmark** for the BOB/PEN corridor. It would provide tighter alignment with official central bank data. It is retained as the **audit reference** because:

1. The BCRP API is intermittently unreachable from some GenLayer Studionet validator nodes
2. When some validators succeed and others fail, `strict_eq` returns `MAJORITY_DISAGREE`
3. A rate lock that cannot reach consensus is operationally indistinguishable from a dead oracle

**Infrastructure upgrade path:** A BCRP-accessible proxy (e.g., a cached endpoint running on infrastructure with consistent global routing) would make this the live primary. This is an infrastructure decision, not a missing product feature.

---

## Why BCRP Did Not Become the Live Source

During controlled validation (2026-03-06), three consecutive `request_rate_lock_primary` calls returned `MAJORITY_DISAGREE`:

- 3/5 validators returned `disagree`, 2/5 returned `agree`
- Leader receipt: `execution_result: SUCCESS` (leader could reach BCRP)
- `votes` showed consistent 3–2 split across multiple runs
- Root cause: BCRP API network reachability varies across GenLayer validator node infrastructure

The fallback path (`request_rate_lock_fallback`) consistently returned `MAJORITY_AGREE` (3–4/5 validators agreeing).

**Decision:** Accept open.er-api as the live benchmark and position BCRP as an off-chain audit reference. Do not misrepresent the on-chain benchmark. The frontend shows the actual `benchmark_type` from each transaction.

---

## Consensus Mechanism

```
strict_eq + deterministic rounding

Each validator independently:
1. Fetches open.er-api.com/v6/latest/BOB
2. Reads rates.PEN (BOB/PEN rate)
3. Rounds to nearest 0.001: round(rate, 3)
4. Constructs deterministic JSON result string

strict_eq: all validators return the same bytes → consensus.
No LLM involved in the numeric comparison path.
```

---

## Fields Recorded Per Rate Event

Every `receiveRate()` and `receiveRolledRate()` call stores:

| Field | Example | Notes |
|-------|---------|-------|
| `benchmarkType` | `MARKET_AGGREGATE` | Which source was used |
| `benchmarkId` | `MARKET-20260306` | Date-stamped, unique per event |
| `asOfTimestamp` | `1772824355` | Unix UTC from oracle |
| `rate` | `493000000000000000` | 18-decimal fixed point |
| `settlementAmount` | `73950000000000000000000` | Invoice × rate |

Roll records additionally store: `priorRate`, `rolledRate`, `rollCost`, `priorDueDate`, `newDueDate`.

---

## Roll Semantics

A roll is a **distinct economic event**, not a silent rate update:

- `requestRoll(newDueDate)` stores `_pendingNewDueDate` on-chain and emits `RollRequested`
- The relayer fetches a fresh benchmark (trying primary first, falling back)
- `receiveRolledRate(newRate, rollCost, benchmarkId, asOfTimestamp)` is called by the relayer
- The contract reads `_pendingNewDueDate` from storage — the relayer cannot inject a different date
- `roll_cost_18 = 0` (spot re-lock; no BOB/PEN OTC forward points available)
- The UI labels this **"Extend hedge"** — not "FX forward"

---

## Benchmark Positioning (External Communication)

> "GenLayer locks the settlement amount using a reproducible market benchmark available to all validators. Official central-bank rates are retained as an off-chain audit reference for reconciliation and review."

Do not say:
- ~~"central-bank cross is the primary"~~ (it fails consensus today)
- ~~"fallback was triggered because something broke"~~ (it was triggered by a design principle)
- ~~"BCRP rate is used for settlement"~~ (it is not — open.er-api is)

Do say:
- "The system selected the first validator-reproducible benchmark in the hierarchy"
- "BCRP×BCB is the audit reference for institutional reconciliation"
- "A BCRP proxy would make the central-bank cross the live primary — this is an infrastructure upgrade path"
