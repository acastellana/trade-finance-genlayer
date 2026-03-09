# End-to-End Test Results — Cross-Chain Trade Finance Dispute Resolution

**Test date:** 2026-03-07/08  
**Status: ✅ PASS — all 3 scenarios produced distinct AI verdicts, delivered and settled on-chain**

---

## System Overview

A fully on-chain trade finance system where:
1. Importer and exporter lock an FX rate and fund escrow on **Base Sepolia**
2. When shipment is disputed, the contract self-registers with **InternetCourtFactory**
3. The **EvmToGenLayer relay** detects the dispute and deploys a single-use Python oracle to **GenLayer Studionet**
4. An **AI jury** (GenLayer consensus protocol) fetches court sheet images from IPFS and evaluates the evidence
5. The verdict travels **GenLayer → BridgeSender → BridgeForwarder (zkSync) → LayerZero V2 → BridgeReceiver → InternetCourtFactory → TradeFxSettlement**
6. Escrow is released (TIMELY), refunded (LATE), or held for manual review (UNDETERMINED)

---

## Scenario Results

### Scenario A — QC-COOP-2026-0003 (TIMELY → SETTLED)

| Field | Value |
|---|---|
| TradeFxSettlement | [`0xb4700D67d6cBeF18011021A2A20389ea88753234`](https://sepolia.basescan.org/address/0xb4700D67d6cBeF18011021A2A20389ea88753234) |
| InternetCourt case ID | 3 |
| GenLayer oracle | [`0x2293B52CdA2940CBD9EFB60d29B88C5Da53FbedE`](https://explorer-studio.genlayer.com/transactions/0x96fd014f7d806ce228525524987575cb8b136ccf323c79d15bd5c1e23ba3be2b) |
| Oracle deploy tx | `0x96fd014f7d806ce228525524987575cb8b136ccf323c79d15bd5c1e23ba3be2b` |
| AI verdict | **TIMELY** |
| Final status | **6 = SETTLED** — exporter received 73,950 PEN |
| shipmentStatus | 3 = TIMELY |
| finalizeAfterShipment tx | `0x35ab0764fc3edd0ed5d42558f6dab6dcecefaf3c2eb51ffa843c843be68fb1e0` |
| contestShipment tx | `0x7c02ae194c6a29a1b63fddcad70ebbad5d962faaa32dfa2725df7df14f93ac52` |

**AI reasoning:** *"Both the ANB customs exit record (2026-04-05 22:41:00 -04:00) and the SUNAT border gate event (2026-04-05 23:12:00 -04:00) confirm the crossing occurred before the deadline of 23:59:59 -04:00, with matching truck plate and container references."*

**Court sheets:**
- Sheet A (ANB): `QmX5ydh3egjapwcwA2uePBsozBZAr3JZi7rGgdKC1N5MQG`
- Sheet B (SUNAT): `Qmex77PpZJaqMfjugbanC316drLcN9BjzGzErf6WgSSeGM`

---

### Scenario B — QC-COOP-2026-0004 (LATE → CANCELLED)

| Field | Value |
|---|---|
| TradeFxSettlement | [`0xbd0dB046A913817522B595CFEb922D5E2aad4268`](https://sepolia.basescan.org/address/0xbd0dB046A913817522B595CFEb922D5E2aad4268) |
| InternetCourt case ID | 4 |
| GenLayer oracle | [`0x435b82AE459Cae242B1C2097034D9d98d3bB5E29`](https://explorer-studio.genlayer.com/transactions/0xc7302e49a80e1997e43fffa7fb2d8b3c2ca9d93e1a8e61d5b73da25d63e5d3aa) |
| Oracle deploy tx | `0xc7302e49a80e1997e43fffa7fb2d8b3c2ca9d93e1a8e61d5b73da25d63e5d3aa` |
| AI verdict | **LATE** |
| Final status | **7 = CANCELLED** — importer refunded 73,950 PEN automatically |
| shipmentStatus | 4 = LATE |
| contestShipment tx | `0x57cb5fa07a798e8faf0c73ce183c02c4ff97a4a0ba329c3a194c475a66c6b879` |

**AI reasoning:** *"Both customs records show the truck crossing at Desaguadero on 2026-04-06 (ANB: 02:15:00 -04:00; SUNAT: 02:47:00 -04:00), which is after the contractual deadline of 2026-04-05 23:59:59 -04:00."*

**Court sheets:**
- Sheet A (ANB): `QmeJdpMNJnx62T2PwCgKhgtaj9e3PPvVRhboF5HLzynSBB`
- Sheet B (SUNAT): `QmX6VTDAaV8uQLVDjgG99HnELDpyo74RfAEv3zP3Y6XPz8`

---

### Scenario C — QC-COOP-2026-0005 (UNDETERMINED → Manual Review)

| Field | Value |
|---|---|
| TradeFxSettlement | [`0xe50A8F382B3B751dBd7053963EbaBB59916f3788`](https://sepolia.basescan.org/address/0xe50A8F382B3B751dBd7053963EbaBB59916f3788) |
| InternetCourt case ID | 5 |
| GenLayer oracle | [`0xb7CC51a5A0D931B04eB07285224e7D52Ee295E48`](https://explorer-studio.genlayer.com/transactions/0x9bcb2b9855eaf90d34175796c0a18569fb9e4bf81869dce98044d27f35846aab) |
| Oracle deploy tx | `0x9bcb2b9855eaf90d34175796c0a18569fb9e4bf81869dce98044d27f35846aab` |
| AI verdict | **UNDETERMINED** |
| Final status | **3 = FUNDED** — manual review window open (14 days) |
| shipmentStatus | 5 = UNDETERMINED |
| contestShipment tx | `0xc9d93c813a336e24e7e484503ec1e02c64038ecb4dc6f1d953e476d0702ba68e` |

**AI reasoning:** *"The truck plate numbers do not match between documents (2291-AKL vs 8834-FMX) and the importer's SUNAT timestamp is unreadable due to degraded ink — insufficient evidence to determine timeliness."*

**Court sheets:**
- Sheet A (ANB): `QmYSCLZAc1ziEJhCvPjAhLeaC2Hhi1rCwsVsAFMwysMMvf`
- Sheet B (SUNAT): `QmZa1Vmt3KWJxo9MtY9pitf3L9iHLL2WoKGpJgS4Tb6eHX`

---

## Deployed Infrastructure (permanent, do not redeploy)

### Base Sepolia
| Contract | Address |
|---|---|
| InternetCourtFactory v2 | `0xd533cB0B52E85b3F506b6f0c28b8f6bc4E449Dda` |
| BridgeReceiver | `0xc3e6aE892A704c875bF74Df46eD873308db15d82` |
| MockBOB | `0xbd0ed9bc00b4dc90096bc3af7b3eb1080b4bc166` |
| MockPEN | `0x08bc87f6511913caa4e127c5e4e91618a37a9719` |
| MockUSDC | `0x58C27C7C1Ff5DBF480c956acf6b119508b6FBa4f` |

### zkSync Sepolia
| Contract | Address |
|---|---|
| BridgeForwarder | `0x95c4E5b042d75528f7df355742e48B298028b3f2` |

### GenLayer Studionet
| Contract | Address |
|---|---|
| BridgeSender | `0xC94bE65Baf99590B1523db557D157fabaD2DA729` |
| FxBenchmarkOracle | `0x3B8501bAcaB70dedbC6f8B8EFCB888ba66cbc73e` |

### Wallets
| Role | Address |
|---|---|
| Exporter / Deployer | `0xe9630ba0e3cc2d3BFC58fbE1Bbde478f06E4CE87` |
| Importer | `0x942C20d078f7417aD67E96714310DA8068850B77` |
| Oracle Relayer | `0x7b9797c4c2DA625b120A27AD2c07bECB7A0E30fa` |

---

## Architecture

```
Exporter / Importer
        │
   Base Sepolia
┌─────────────────────────────┐
│  TradeFxSettlement.sol      │  requestRateLock → receiveRate → fundSettlement
│  (per trade)                │  contestShipment() → factory.registerCase()
└─────────────────────────────┘
                │ DisputeRequested(caseAddress)
                │ emitted by InternetCourtFactory
                ▼
   EvmToGenLayer relay (Node.js, 5s poll)
   reads: getOracleType() → TRADE_FINANCE_V1
   reads: getOracleArgs() → (caseId, settlement, stmt, guideline, sheetACid, sheetBCid)
   looks up: ORACLE_REGISTRY[TRADE_FINANCE_V1] → ShipmentDeadlineCourt.py
                │
                ▼ deployContract(code, args)
   ┌─────────────────────────────┐
   │  ShipmentDeadlineCourt.py  │  GenLayer Studionet
   │  (single-use oracle)       │  fetches court sheets from IPFS
   │  AI jury consensus         │  evaluates evidence images
   │  → TIMELY / LATE / UNDET.  │  sends verdict to BridgeSender
   └─────────────────────────────┘
                │ bridge.emit().send_message(eid, factory, payload)
                ▼
   BridgeSender.py (GenLayer) accumulates messages
                │
   GenLayerToEvm relay reads pending messages
                │
                ▼ callRemoteArbitrary(eid, factory, payload)
   ┌─────────────────────────────┐
   │  BridgeForwarder.sol       │  zkSync Sepolia — LayerZero endpoint
   └─────────────────────────────┘
                │ LayerZero V2
                ▼
   ┌─────────────────────────────┐
   │  BridgeReceiver.sol        │  Base Sepolia
   └─────────────────────────────┘
                │ processBridgeMessage(srcChain, sender, payload)
                ▼
   ┌─────────────────────────────┐
   │  InternetCourtFactory.sol  │  decodes (caseAddress, resolutionData)
   │                            │  calls IResolutionTarget(caseAddress)
   │                            │  .setResolution(verdict, reason)
   └─────────────────────────────┘
                │
                ▼
   ┌─────────────────────────────┐
   │  TradeFxSettlement.sol     │
   │  TIMELY → status=TIMELY    │  exporter calls finalizeAfterShipment()
   │           → SETTLED (6)    │  → exporter receives PEN
   │  LATE   → CANCELLED (7)    │  → importer refunded PEN automatically
   │  UNDET. → manual review    │  → arbitrator or 14-day timeout
   └─────────────────────────────┘
```

## IResolutionTarget Interface — Generic Case Integration

Any contract can plug into InternetCourt by implementing:

```solidity
interface IResolutionTarget {
    // Called by factory when verdict arrives from GenLayer
    function setResolution(uint8 verdict, string calldata reasonSummary) external;
    
    // Oracle type — determines which Python oracle to deploy
    // e.g. keccak256("TRADE_FINANCE_V1") or keccak256("AGENT_DISPUTE_V1")
    function getOracleType() external view returns (bytes32);
    
    // ABI-encoded args passed to oracle constructor (type-specific)
    function getOracleArgs() external view returns (bytes memory);
}
```

`getOracleArgs()` schema for `TRADE_FINANCE_V1`:
```
abi.encode(
  string  caseId,              // "QC-COOP-2026-0003"
  address settlementContract,  // TradeFxSettlement address
  string  statement,           // dispute statement (evaluated by AI)
  string  guidelineVersion,    // "shipment-deadline-v1"
  string  courtSheetACid,      // IPFS CID of exporter evidence image
  string  courtSheetBCid       // IPFS CID of importer evidence image
)
```

Adding a new case type requires only:
1. A Python oracle file in `internetcourt/contracts/bridge/`
2. One new entry in `ORACLE_REGISTRY` in `EvmToGenLayer.ts`

## Running the Demo

### Prerequisites
```bash
# Install deps
cd projects/conditional-payment-cross-border-trade && npm install
cd projects/internetcourt/bridge/service && npm install
```

### Deploy fresh scenario contracts
```bash
cd projects/conditional-payment-cross-border-trade

# Regenerate court sheet images (if needed)
python3 scripts/generate_court_sheets.py

# Upload images to IPFS (updates artifacts/new_court_sheet_cids_v2.json)
node scripts/upload-cids.mjs   # or inline in deploy-v5-scenarios.mjs

# Deploy 3 TradeFxSettlement contracts → rate lock → fund → contest
node scripts/deploy-v5-scenarios.mjs
```

### Run relay service
```bash
cd projects/internetcourt/bridge/service

# Configure environment
cp .env.example .env
# Set: RELAY_PRIVATE_KEY, GENLAYER_RPC_URL, BASE_RPC_URL, etc.

npm start
# Polls Base every 5s for DisputeRequested events
# Deploys ShipmentDeadlineCourt.py oracle to GenLayer per dispute
# Waits for AI consensus (~2-6 min)
# Routes verdict back via LayerZero → factory → settlement contract
```

### Finalize TIMELY verdict (exporter action)
```bash
cast send <settlement_addr> "finalizeAfterShipment()" \
  --private-key <exporter_key> \
  --rpc-url https://sepolia.base.org
```

## Known Testnet Limitations

- **LayerZero bridge path requires zkSync Sepolia ETH** on the relayer wallet (`0x7b979...`). When unavailable, verdicts are delivered via `resolveShipmentVerdict()` direct fallback on Base Sepolia (using `oracleRelayer` role). Production deployment eliminates this by funding the relayer.
- **GenLayer Studionet** is a public shared testnet; oracle finalization can take 2–6 minutes depending on validator load.
- **Court sheet images on IPFS** use public gateways; fetches may occasionally fail in non-deterministic blocks, causing UNDETERMINED fallback.
