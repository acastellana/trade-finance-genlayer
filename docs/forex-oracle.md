# GenLayer Forex Oracle — BOB/PEN

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  GenLayer Studionet                      │
│                                                          │
│   ForexOracle.py  (intelligent contract)                 │
│   ┌──────────────────────────────────────────────────┐   │
│   │  update_rate() — 5 AI validators each:           │   │
│   │    1. open.er-api.com  → JSON rate               │   │
│   │    2. xe.com           → scrape + LLM extract    │   │
│   │    3. wise.com         → scrape + LLM extract    │   │
│   │                                                  │   │
│   │  Consensus: all sources within 50 bps (0.5%)     │   │
│   │  Reverts if any source deviates beyond tolerance  │   │
│   │                                                  │   │
│   │  Stores:  rate_str = "0.4948"                    │   │
│   │           rate_18  = 494800000000000000           │   │
│   └──────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────┘
                                  │ forex-oracle.mjs reads rate_18
                                  ▼
┌─────────────────────────────────────────────────────────┐
│                  Base Sepolia                            │
│                                                          │
│   GenLayerForexOracle.sol                                │
│   ┌──────────────────────────────────────────────────┐   │
│   │  commitRate(rate_18)  ← relayer pushes GL rate   │   │
│   │  getRate()            → returns rate_18          │   │
│   │  isStale()            → true if > 24h old        │   │
│   └──────────────────────────────────────────────────┘   │
│                        │                                  │
│                        ▼                                  │
│   TradeFinanceEscrow (reads oracle at construction)       │
│   lockedForexRate = oracle.getRate()  ← immutable        │
└─────────────────────────────────────────────────────────┘
```

## What the GenLayer contract does

- **3 independent sources**: `open.er-api.com` (JSON API), `xe.com` (scraped), `wise.com` (scraped)
- **5 validators** each fetch from all 3 sources independently
- **Tolerance check**: if any source deviates > 50 bps from the median, the transaction reverts
- **Rate stored on-chain**: `rate_18` (18-decimal fixed point, Solidity-compatible)

## How to get a live rate for a new escrow

### Step 1 — Trigger GenLayer oracle

```bash
# Full flow: deploy oracle (once) + update rate + print result
node scripts/forex-oracle.mjs run

# Or if oracle is already deployed:
node scripts/forex-oracle.mjs update <oracle_address>
node scripts/forex-oracle.mjs read   <oracle_address>
```

The oracle address is cached in `artifacts/forex-oracle-address.json`.

Output looks like:
```
┌─────────────────────────────────────────────┐
│  BOB/PEN rate:     0.494800                 │
│  rate_18 (Solidity):                        │
│  494800000000000000                         │
│  Tolerance:        50 bps                   │
│  Last updated:     2026-03-06T12:00:00.000Z │
└─────────────────────────────────────────────┘

📋 To use in Foundry deploy script:
   export FOREX_RATE_18=494800000000000000
```

### Step 2 — Deploy GenLayerForexOracle on Base Sepolia

```bash
export PATH="$HOME/.foundry/bin:$PATH"
export RELAYER_KEY=$(cat ~/.internetcourt/.exporter_key)
export FOREX_RATE_18=494800000000000000   # from Step 1

forge script script/DeployForexOracle.s.sol \
  --rpc-url https://sepolia.base.org \
  --broadcast -vvv
```

This deploys `GenLayerForexOracle` and immediately commits the rate.

### Step 3 — Deploy escrow using the oracle

```bash
export FOREX_ORACLE=<oracle_address_from_step_2>

forge script script/Deploy.s.sol \
  --rpc-url https://sepolia.base.org \
  --broadcast -vvv
```

The escrow constructor calls `oracle.getRate()` to lock the rate permanently.

## Staleness guard

The `GenLayerForexOracle` contract rejects `getRate()` calls if the rate is older than 24 hours. Before deploying an escrow you must ensure:
1. The GenLayer oracle was updated within the last 24 hours
2. The on-chain oracle has the committed rate

## Adding a real on-chain Chainlink feed (future)

When BOB/USD and PEN/USD Chainlink feeds exist:
1. Replace `GenLayerForexOracle` with a `ChainlinkForexOracle` wrapper
2. It derives BOB/PEN = (BOB/USD) / (PEN/USD) from two price feeds
3. No relayer needed — fully trust-minimized

Until then, the GenLayer oracle provides a strong alternative: 3 data sources + 5 AI validators + on-chain commitment.
