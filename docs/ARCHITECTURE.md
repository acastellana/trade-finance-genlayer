# Cross-Chain Trade Finance Dispute Resolution — Architecture

## Overview

A fully on-chain trade finance settlement system where AI-powered arbitration (GenLayer InternetCourt) resolves shipment deadline disputes, with verdicts delivered cross-chain via LayerZero V2 to release or cancel escrow on Base Sepolia.

```
Exporter / Importer
        │
   Base Sepolia
┌─────────────────────────┐
│  TradeFxSettlement.sol  │◄── rate lock, fund, contest
│  (per trade)            │──► registerCase() ──► InternetCourtFactory
└─────────────────────────┘         │
                                    │ DisputeRequested event
                                    ▼
                              EvmToGenLayer relay (Node.js)
                              reads getOracleType() + getOracleArgs()
                                    │
                                    ▼ deploy contract
                         ┌─────────────────────────┐
                         │  ShipmentDeadlineCourt.py│  GenLayer Studionet
                         │  (single-use oracle)     │
                         │  AI jury consensus       │
                         │  fetches court sheets    │
                         │  from IPFS               │
                         └─────────────────────────┘
                                    │ verdict → bridge.send_message()
                                    ▼
                         ┌─────────────────────────┐
                         │  BridgeSender.py        │  GenLayer
                         └─────────────────────────┘
                                    │
                                    ▼ GenLayerToEvm relay
                         ┌─────────────────────────┐
                         │  BridgeForwarder.sol    │  zkSync Sepolia
                         └─────────────────────────┘
                                    │ LayerZero V2
                                    ▼
                         ┌─────────────────────────┐
                         │  BridgeReceiver.sol     │  Base Sepolia
                         └─────────────────────────┘
                                    │ processBridgeMessage()
                                    ▼
                         ┌─────────────────────────┐
                         │  InternetCourtFactory   │  Base Sepolia
                         │  IResolutionTarget      │
                         │  .setResolution()       │
                         └─────────────────────────┘
                                    │
                                    ▼
                         ┌─────────────────────────┐
                         │  TradeFxSettlement.sol  │
                         │  TIMELY → SETTLED       │
                         │  LATE → CANCELLED       │
                         │  UNDETERMINED → manual  │
                         └─────────────────────────┘
```

## Smart Contracts

### Base Sepolia

| Contract | Address | Description |
|---|---|---|
| InternetCourtFactory v2 | `0xd533cB0B52E85b3F506b6f0c28b8f6bc4E449Dda` | Case registry + verdict router |
| BridgeReceiver | `0xc3e6aE892A704c875bF74Df46eD873308db15d82` | LayerZero endpoint receiver |
| MockBOB | `0xbd0ed9bc00b4dc90096bc3af7b3eb1080b4bc166` | Boliviano test token |
| MockPEN | `0x08bc87f6511913caa4e127c5e4e91618a37a9719` | Peruvian sol test token |
| MockUSDC | `0x58C27C7C1Ff5DBF480c956acf6b119508b6FBa4f` | Reference stablecoin |

### zkSync Sepolia

| Contract | Address | Description |
|---|---|---|
| BridgeForwarder | `0x95c4E5b042d75528f7df355742e48B298028b3f2` | LayerZero message sender |

### GenLayer Studionet

| Contract | Address | Description |
|---|---|---|
| BridgeSender | `0xC94bE65Baf99590B1523db557D157fabaD2DA729` | Cross-chain message emitter |
| FxBenchmarkOracle | `0x3B8501bAcaB70dedbC6f8B8EFCB888ba66cbc73e` | FX rate oracle |

## Demo Scenarios

Three real trade cases, each producing a distinct AI verdict:

| Case | Contract | Court Sheets | AI Verdict | Final Status |
|---|---|---|---|---|
| QC-COOP-2026-0003 (A) | TBD | ANB exit 22:41 + SUNAT gate 23:12 APR 05 | TIMELY | SETTLED — exporter paid |
| QC-COOP-2026-0004 (B) | TBD | ANB exit 02:15 + SUNAT gate 02:47 APR 06 | LATE | CANCELLED — importer refunded |
| QC-COOP-2026-0005 (C) | TBD | ANB exit 23:52 + SUNAT gate illegible | UNDETERMINED | Manual review (14-day window) |

**Trade route:** Bolivia → Peru via Desaguadero land border
**Parties:** Minera Andina SRL (exporter, Bolivia) vs Electroquímica del Perú S.A. (importer, Peru)
**Terms:** 150,000 BOB @ 0.493 PEN/BOB = 73,950 PEN · Deadline: 2026-04-05T23:59:59-04:00

## Evidence Documents (IPFS)

Court sheets are PNG images generated from authentic customs record layouts:
- **Sheet A** (exporter): ANB (Aduana Nacional de Bolivia) Customs Exit Certificate
- **Sheet B** (importer): SUNAT Border Gate Event Record

The AI jury fetches both images from IPFS during consensus evaluation.

## Verdict Encoding

| Verdict | uint8 | Action |
|---|---|---|
| TIMELY | 1 | `settleShipment()` → exporter receives PEN |
| LATE | 2 | `cancelShipment()` → importer refunded PEN |
| UNDETERMINED | 3 | Manual review window opens (14 days) |

## Contract Status Enums

```
Status: 0=DRAFT, 1=RATE_PENDING, 2=RATE_LOCKED, 3=FUNDED, 4=ROLL_PENDING, 5=ROLLED, 6=SETTLED, 7=CANCELLED
ShipmentStatus: 0=NONE, 1=ACCEPTED, 2=CONTESTED, 3=TIMELY, 4=LATE, 5=UNDETERMINED
```

## IResolutionTarget Interface

Any external contract can integrate with InternetCourt via this interface:

```solidity
interface IResolutionTarget {
    function setResolution(uint8 verdict, string calldata reasonSummary) external;
    function getOracleType() external view returns (bytes32);
    function getOracleArgs() external view returns (bytes memory);
}
```

The relay reads `getOracleType()` to look up the oracle Python source in `ORACLE_REGISTRY`, then decodes `getOracleArgs()` to build the oracle constructor args. Adding a new case type requires only:
1. A Python oracle in `internetcourt/contracts/bridge/`
2. A new `ORACLE_REGISTRY` entry in `EvmToGenLayer.ts`

## Running the System

### Prerequisites
- Node.js 18+, Foundry, Python 3.10+
- Wallets funded: exporter + importer on Base Sepolia; relayer on Base + zkSync Sepolia
- GenLayer Studionet API key

### Deploy fresh scenario contracts
```bash
cd projects/conditional-payment-cross-border-trade
node scripts/deploy-v5-scenarios.mjs
```

### Run relay service
```bash
cd projects/internetcourt/bridge/service
cp .env.example .env   # fill in keys
npm start
```

The relay polls Base Sepolia every 5s for `DisputeRequested` events, deploys oracles to GenLayer, waits for AI consensus, and routes verdicts back via LayerZero.

### Generate court sheet evidence images
```bash
cd projects/conditional-payment-cross-border-trade
python3 scripts/generate_court_sheets.py
# Upload to IPFS:
node -e "..." # see scripts/deploy-v5-scenarios.mjs upload section
```
