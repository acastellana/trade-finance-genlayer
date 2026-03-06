#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"

RPC=https://sepolia.base.org
FACTORY=0xb981298fb5E1D27ade6f88014C2f24c30137BC9a
USDC=0x58C27C7C1Ff5DBF480c956acf6b119508b6FBa4f
CHAIN_ID=84532
DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$DIR/state.json"

ts() { date -u '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] [$1] $2"; }
banner() { echo -e "\n$(printf '═%.0s' {1..78})\n$1\n$(printf '═%.0s' {1..78})"; }

# ═══════════════════════════════════════════════════════════
# STEP 0: Load wallets from ~/.internetcourt/
# (exporter also acts as deployer — saves needing a 3rd funded wallet)
# ═══════════════════════════════════════════════════════════
banner "STEP 0: Load wallets"

EXPORTER_KEY=$(cat ~/.internetcourt/.exporter_key)
EXPORTER_ADDR=$(python3 -c "import json; print(json.load(open('/home/albert/.internetcourt/exporter.json'))['address'])")
IMPORTER_KEY=$(cat ~/.internetcourt/.importer_key)
IMPORTER_ADDR=$(python3 -c "import json; print(json.load(open('/home/albert/.internetcourt/importer.json'))['address'])")

# Deployer = exporter (same funded wallet)
DEPLOYER_KEY=$EXPORTER_KEY
DEPLOYER_ADDR=$EXPORTER_ADDR

log "WALLET" "Deployer:  $DEPLOYER_ADDR"
log "WALLET" "Exporter:  $EXPORTER_ADDR"
log "WALLET" "Importer:  $IMPORTER_ADDR"

# ═══════════════════════════════════════════════════════════
# STEP 0.5: Check ETH balances (need gas)
# ═══════════════════════════════════════════════════════════
banner "STEP 0.5: Check ETH balances"

for name_addr in "deployer:$DEPLOYER_ADDR" "exporter:$EXPORTER_ADDR" "importer:$IMPORTER_ADDR"; do
  IFS=: read -r name addr <<< "$name_addr"
  bal=$(cast balance "$addr" --rpc-url "$RPC" --ether 2>/dev/null || echo "0")
  log "ETH" "$name ($addr): $bal ETH"
  
  # Check if balance is essentially zero
  if [ "$bal" = "0.000000000000000000" ] || [ "$bal" = "0" ]; then
    echo ""
    echo "⚠️  $name has no Base Sepolia ETH!"
    echo "   Fund it from: https://www.alchemy.com/faucets/base-sepolia"
    echo "   Address: $addr"
    echo ""
    echo "Run this script again after funding all 3 wallets."
    exit 1
  fi
done

log "ETH" "All wallets funded ✅"

# ═══════════════════════════════════════════════════════════
# STEP 1: Deploy sBOB + sPEN + TradeFinanceEscrow
# ═══════════════════════════════════════════════════════════
banner "STEP 1: Deploy contracts"

cd "$DIR"

DEPLOYER_KEY=$DEPLOYER_KEY \
EXPORTER_ADDR=$EXPORTER_ADDR \
IMPORTER_ADDR=$IMPORTER_ADDR \
forge script script/Deploy.s.sol:Deploy \
  --rpc-url "$RPC" \
  --broadcast \
  --chain-id $CHAIN_ID \
  -vvv 2>&1 | tee /tmp/deploy-output.txt

# Parse deployed addresses from output
SBOB=$(grep "sBOB deployed at:" /tmp/deploy-output.txt | awk '{print $NF}')
SPEN=$(grep "sPEN deployed at:" /tmp/deploy-output.txt | awk '{print $NF}')
ESCROW=$(grep "TradeFinanceEscrow deployed at:" /tmp/deploy-output.txt | awk '{print $NF}')

log "DEPLOY" "sBOB:   $SBOB"
log "DEPLOY" "sPEN:   $SPEN"
log "DEPLOY" "Escrow: $ESCROW"

# ═══════════════════════════════════════════════════════════
# STEP 2: Importer funds escrow (250,000 sPEN)
# ═══════════════════════════════════════════════════════════
banner "STEP 2: Importer funds escrow"

ESCROW_AMOUNT="250000000000000000000000"  # 250,000e18

log "TX" "Importer approves sPEN spending..."
cast send "$SPEN" "approve(address,uint256)" "$ESCROW" "$ESCROW_AMOUNT" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "TX" "Importer deposits sPEN into escrow..."
cast send "$ESCROW" "fundEscrow(uint256)" "$ESCROW_AMOUNT" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "ESCROW" "Funded with 250,000 sPEN ✅"

# ═══════════════════════════════════════════════════════════
# STEP 3: Exporter submits shipment
# ═══════════════════════════════════════════════════════════
banner "STEP 3: Exporter submits shipment"

cast send "$ESCROW" "submitShipment(string)" "COSCO B/L COSU-BOL-2026-001847 — MV COSCO ATACAMA — Antofagasta to Callao" \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "SHIP" "Shipment submitted ✅"

# ═══════════════════════════════════════════════════════════
# STEP 4: Importer confirms delivery
# ═══════════════════════════════════════════════════════════
banner "STEP 4: Importer confirms delivery"

cast send "$ESCROW" "confirmDelivery()" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "DELIVERY" "Delivery confirmed ✅"

# ═══════════════════════════════════════════════════════════
# STEP 5: Create InternetCourt dispute
# ═══════════════════════════════════════════════════════════
banner "STEP 5: Create InternetCourt dispute via factory"

# Mint MockUSDC for IC bond (importer creates the agreement)
log "TX" "Minting MockUSDC for IC bond..."
cast send "$USDC" "mint(address,uint256)" "$IMPORTER_ADDR" 100000000 \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

USDC_ESCROW=50000000  # 50 USDC bond
JOIN_DEADLINE=$(( $(date +%s) + 86400 ))

log "TX" "Approving MockUSDC for factory..."
cast send "$USDC" "approve(address,uint256)" "$FACTORY" "$USDC_ESCROW" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

# Get next agreement ID before creating
NEXT_ID=$(cast call "$FACTORY" "nextAgreementId()(uint256)" --rpc-url "$RPC" 2>/dev/null)
log "IC" "Next IC case ID will be: $NEXT_ID"

STATEMENT="Minera Andina SRL delivered 50 metric tons of battery-grade lithium carbonate (Li2CO3) meeting ISO 6206:2023 purity standards (minimum 99.0%) to Callao port, Peru, in conformity with Purchase Order EP-PO-2026-0178. The goods were in merchantable condition upon loading at Antofagasta under CIF Incoterms 2020."

GUIDELINES="Evaluate the following:
1. Was the material at or above 99.0% Li2CO3 purity when loaded at Antofagasta? Consider pre-shipment SGS Certificate of Analysis (ISO/IEC 17025 accredited).
2. Did purity degrade upon arrival in Callao? Consider Bureau Veritas independent analysis.
3. Were shipping containers in adequate condition? Consider inspection reports for seal integrity and moisture damage.
4. Under CIF Incoterms 2020, risk passes to buyer when goods cross the ship's rail at port of loading. Transit damage is buyer's marine insurance claim.
5. Weigh accredited lab results (ISO/IEC 17025) more heavily than unaccredited claims.
6. If pre-shipment lab shows compliant and arrival lab shows non-compliant, assess whether degradation was caused by shipping conditions or original quality."

EVIDENCE_DEFS='{"party_a":{"max_chars":10000,"description":"Proof that goods met ISO 6206 at loading: SGS Certificate of Analysis (99.12% purity), SGS Pre-Shipment Inspection Report, COSCO Bill of Lading with container seal records"},"party_b":{"max_chars":10000,"description":"Proof that goods did not meet spec: Bureau Veritas arrival analysis (98.54% avg purity), arrival inspection showing container damage, formal rejection notice with claimed damages"}}'

log "TX" "Creating IC agreement (importer = Party A, exporter = Party B)..."
# NOTE: Importer creates (pays USDC bond). If Party A wins → importer claim.
# But for trade: exporter should be Party A (statement supports exporter).
# So EXPORTER creates the agreement.

# Actually, let's have EXPORTER create it. The statement favors the exporter.
# Mint USDC for exporter too.
cast send "$USDC" "mint(address,uint256)" "$EXPORTER_ADDR" 100000000 \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

cast send "$USDC" "approve(address,uint256)" "$FACTORY" "$USDC_ESCROW" \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

cast send "$FACTORY" \
  "createAgreement(address,string,string,string,uint256,address,uint256,uint256,uint256,string)" \
  "$IMPORTER_ADDR" \
  "$STATEMENT" \
  "$GUIDELINES" \
  "$EVIDENCE_DEFS" \
  86400 \
  "$USDC" \
  "$USDC_ESCROW" \
  "$JOIN_DEADLINE" \
  10000 \
  "" \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

sleep 3

# Look up agreement address
AGREEMENT=$(cast call "$FACTORY" "agreements(uint256)(address)" "$NEXT_ID" --rpc-url "$RPC" 2>/dev/null)
log "IC" "IC Case #$NEXT_ID created: $AGREEMENT"

# Link dispute to our escrow
cast send "$ESCROW" "raiseDispute(address)" "$AGREEMENT" \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "DISPUTE" "Trade escrow linked to IC Case #$NEXT_ID ✅"

# ═══════════════════════════════════════════════════════════
# STEP 6: Importer accepts IC agreement
# ═══════════════════════════════════════════════════════════
banner "STEP 6: Importer accepts IC agreement"

cast send "$AGREEMENT" "acceptAgreement()" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "IC" "Importer accepted IC agreement ✅"

# ═══════════════════════════════════════════════════════════
# STEP 7: Both parties disagree → raise dispute
# ═══════════════════════════════════════════════════════════
banner "STEP 7: Parties disagree → raise IC dispute"

# Exporter proposes TRUE (Party A wins = exporter wins)
cast send "$AGREEMENT" "proposeOutcome(bool)" true \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

# Importer proposes FALSE (Party B wins = importer wins... wait that's wrong)
# Actually: Party A = exporter (creator). true = PARTY_A wins = exporter.
# Importer should propose false = PARTY_B wins = importer.
cast send "$AGREEMENT" "proposeOutcome(bool)" false \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "IC" "Parties disagree: exporter=TRUE, importer=FALSE"

# Raise dispute
cast send "$AGREEMENT" "raiseDispute()" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "IC" "IC dispute raised ✅"

# ═══════════════════════════════════════════════════════════
# STEP 8: Submit evidence
# ═══════════════════════════════════════════════════════════
banner "STEP 8: Both parties submit evidence"

EVIDENCE_BASE="https://raw.githubusercontent.com/acastellana/apps/main/trade-finance/evidence"

EXPORTER_EVIDENCE="EXPORTER EVIDENCE — Minera Andina SRL (Party A)

1. PURITY CERTIFICATION: SGS Chile Certificate of Analysis (Report CL-ANT-2026-04871) confirms Li2CO3 purity of 99.12%, tested per ISO 6206:2023 by ICP-OES at SGS's ISO/IEC 17025 accredited laboratory (Accreditation LE-1247). All 10 analytical parameters PASS. Image: ${EVIDENCE_BASE}/01_SGS_Certificate_of_Analysis.jpg

2. PRE-SHIPMENT INSPECTION: SGS Pre-Shipment Inspection Report (CL-ANT-PSI-2026-01203) dated 2026-01-22 confirms all 2,000 bags (50 MT) in perfect condition. All 4 containers inspected — clean, dry, structurally sound. SGS bolt seals applied (SGS-CL-880214 through 880217). Zero defects. Desiccant strips installed per ISO 7096. Image: ${EVIDENCE_BASE}/02_SGS_PreShipment_Inspection.jpg

3. BILL OF LADING: COSCO Bill of Lading (COSU-BOL-2026-001847) confirms goods loaded in apparent good order and condition on 2026-01-24 aboard MV COSCO ATACAMA. Image: ${EVIDENCE_BASE}/03_COSCO_Bill_of_Lading.jpg

4. LEGAL POSITION: Under CIF Incoterms 2020, risk transferred to buyer when goods crossed the ship's rail at Antofagasta. Any transit degradation — including the moisture damage Party B alleges in containers 3 and 4 — is the buyer's marine insurance claim, not the seller's liability. The SGS seals were intact at loading; any seal degradation occurred after risk transfer."

IMPORTER_EVIDENCE="IMPORTER EVIDENCE — Electroquimica del Peru SA (Party B)

1. INDEPENDENT ANALYSIS: Bureau Veritas Lima (Report BV-LIM-2026-AN-00412) independent analysis by ICP-MS at INACAL-DA accredited laboratory (LP-042-2024) shows: Containers 1 & 2: 98.87% purity. Containers 3 & 4: 97.54% purity (severe degradation). Weighted average: 98.54% — 0.46 percentage points BELOW the ISO 6206 minimum of 99.0%. Image: ${EVIDENCE_BASE}/04_BureauVeritas_Lab_Analysis.jpg

2. ARRIVAL INSPECTION: Internal inspection report (EP-QC-INS-2026-0089) documents: Container COSCU-123458-3 had corroded seal (SGS-CL-880216) with visible fracture. Container COSCU-123459-1 had degraded door gasket with moisture on interior walls and approximately 15 damp bags. 62 photographs taken. 2 of 4 containers REJECTED. Image: ${EVIDENCE_BASE}/05_Arrival_Inspection_Report.jpg

3. CRITICAL: Even containers 1 and 2 with INTACT seals test at 98.87% — still below the 99.0% minimum. This suggests the original material may not have been as pure as the SGS certificate claims. Note that SGS used ICP-OES while BV used ICP-MS (higher sensitivity method).

4. FORMAL REJECTION: Formal rejection notice sent to Minera Andina demanding either full replacement shipment or 35% price reduction plus USD 34,000 in consequential damages for production delays. Image: ${EVIDENCE_BASE}/06_Formal_Rejection_Notice.jpg

5. CONTRACT REFERENCE: Purchase Contract ISPA-2025-BOL-PER-0047 specifies minimum 99.0% Li2CO3 purity per ISO 6206:2023, with ICC arbitration clause and GenLayer InternetCourt as supplementary dispute mechanism. Image: ${EVIDENCE_BASE}/07_Purchase_Contract_Excerpt.jpg"

log "TX" "Exporter submitting evidence..."
cast send "$AGREEMENT" "submitEvidence(string)" "$EXPORTER_EVIDENCE" \
  --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "TX" "Importer submitting evidence..."
cast send "$AGREEMENT" "submitEvidence(string)" "$IMPORTER_EVIDENCE" \
  --private-key "$IMPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'

log "EVIDENCE" "Both parties submitted evidence ✅"
log "JURY" "AI jury on GenLayer will now evaluate via cross-chain bridge..."

# ═══════════════════════════════════════════════════════════
# STEP 9: Wait for verdict
# ═══════════════════════════════════════════════════════════
banner "STEP 9: Waiting for AI jury verdict..."

log "JURY" "Checking IC case status every 30s (verdict arrives via GenLayer bridge)..."

for i in $(seq 1 60); do
  IC_STATUS=$(cast call "$AGREEMENT" "status()(uint8)" --rpc-url "$RPC" 2>/dev/null)
  
  case $IC_STATUS in
    3) log "POLL" "Status: RESOLVING (AI jury deliberating)... [$i/60]" ;;
    4)
      VERDICT=$(cast call "$AGREEMENT" "verdict()(uint8)" --rpc-url "$RPC" 2>/dev/null)
      REASONING=$(cast call "$AGREEMENT" "reasoning()(string)" --rpc-url "$RPC" 2>/dev/null)
      
      case $VERDICT in
        1) VERDICT_NAME="PARTY_A (Exporter wins)" ;;
        2) VERDICT_NAME="PARTY_B (Importer wins)" ;;
        *) VERDICT_NAME="UNDETERMINED" ;;
      esac
      
      log "VERDICT" "⚖️ AI JURY VERDICT: $VERDICT_NAME"
      log "VERDICT" "Reasoning: $REASONING"
      break
      ;;
    2) log "POLL" "Status: DISPUTED (waiting for bridge)... [$i/60]" ;;
    *) log "POLL" "Status: $IC_STATUS [$i/60]" ;;
  esac
  
  sleep 30
done

if [ "$IC_STATUS" != "4" ]; then
  log "WARN" "Verdict not received within 30 minutes. Check case on internetcourt.org/cases"
  log "INFO" "Case #$NEXT_ID: $AGREEMENT"
fi

# ═══════════════════════════════════════════════════════════
# STEP 10: Resolve trade escrow based on verdict
# ═══════════════════════════════════════════════════════════
if [ "$IC_STATUS" = "4" ]; then
  banner "STEP 10: Resolve trade escrow from IC verdict"
  
  cast send "$ESCROW" "resolveFromCourt()" \
    --private-key "$EXPORTER_KEY" --rpc-url "$RPC" --json 2>/dev/null | jq -r '.transactionHash'
  
  FINAL_STATUS=$(cast call "$ESCROW" "status()(uint8)" --rpc-url "$RPC" 2>/dev/null)
  COURT_VERDICT=$(cast call "$ESCROW" "courtVerdict()(uint8)" --rpc-url "$RPC" 2>/dev/null)
  
  log "RESOLVED" "Trade escrow resolved ✅"
  log "RESOLVED" "Court verdict: $COURT_VERDICT"
  log "RESOLVED" "Escrow status: $FINAL_STATUS (6=Resolved)"
fi

# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════
banner "SUMMARY"

echo "
Contracts:
  sBOB (Synthetic Boliviano):  $SBOB
  sPEN (Synthetic Sol):        $SPEN
  TradeFinanceEscrow:          $ESCROW

InternetCourt:
  Factory:   $FACTORY
  Case #$NEXT_ID: $AGREEMENT
  View at: https://internetcourt.org/cases

Parties:
  Exporter (Minera Andina SRL):        $EXPORTER_ADDR
  Importer (Electroquimica del Peru):  $IMPORTER_ADDR

Explorer:
  sBOB:   https://sepolia.basescan.org/address/$SBOB
  sPEN:   https://sepolia.basescan.org/address/$SPEN
  Escrow: https://sepolia.basescan.org/address/$ESCROW
  IC:     https://sepolia.basescan.org/address/$AGREEMENT
"

# Save state
cat > "$STATE_FILE" << EOF
{
  "chain": "base-sepolia",
  "sBOB": "$SBOB",
  "sPEN": "$SPEN",
  "escrow": "$ESCROW",
  "icFactory": "$FACTORY",
  "icCaseId": $NEXT_ID,
  "icAgreement": "$AGREEMENT",
  "exporter": "$EXPORTER_ADDR",
  "importer": "$IMPORTER_ADDR"
}
EOF

log "DONE" "State saved to $STATE_FILE"
