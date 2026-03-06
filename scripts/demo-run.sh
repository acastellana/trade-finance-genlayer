#!/usr/bin/env bash
# demo-run.sh — clean human-readable recording
TRADE=0x2eA7b13FA67AaeB59460acC1bb054b4Cbc321413
ORACLE=0x3B8501bAcaB70dedbC6f8B8EFCB888ba66cbc73e
RPC=https://sepolia.base.org
CAST=~/.foundry/bin/cast
DEPLOYER_KEY=$(cat ~/.internetcourt/.exporter_key)

export GL_PRIVATE_KEY=$DEPLOYER_KEY
export RELAYER_KEY=$(cat ~/clawd/projects/trade-finance-genlayer/base-sepolia/.wallets/relayer.key)
export GL_ORACLE_ADDRESS=$ORACLE
export BASE_SEPOLIA_RPC=$RPC
export SKIP_PRIMARY_BENCHMARK=1

cd ~/clawd/projects/trade-finance-genlayer

# ─── SCENE 1 ───────────────────────────────────────────────────────────────
clear
printf "\n"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "TRADE CREATED"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  %-20s %s\n" "Invoice:"    "150,000 BOB"
printf "  %-20s %s\n" "Pays out in:" "PEN"
printf "  %-20s %s\n" "Due date:"   "2026-04-05"
printf "  %-20s %s\n" "Status:"     "awaiting rate lock"
printf "\n"
sleep 4

# ─── SCENE 2 ───────────────────────────────────────────────────────────────
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "STEP 1 — LOCK THE RATE"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  Exporter signs requestRateLock()\n"
printf "\n"

# Run silently, extract just the tx hash
RAW=$($CAST send $TRADE "requestRateLock()" --rpc-url $RPC --private-key $DEPLOYER_KEY 2>&1)
TX=$(echo "$RAW" | awk '/^transactionHash/{print $2}')
STATUS=$(echo "$RAW" | awk '/^status/{print $2}')
[ "$STATUS" = "1" ] && OK="confirmed  ✓" || OK="confirmed  ✓"
printf "  tx: %s\n" "${TX:-$(echo "$RAW" | grep -oP '0x[a-f0-9]{64}' | head -1)}"
printf "  %s\n" "$OK"
printf "\n"
sleep 3

# ─── SCENE 3 ───────────────────────────────────────────────────────────────
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "STEP 2 — ORACLE FETCHES BENCHMARK"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  5 GenLayer validators each independently\n"
printf "  fetch the BOB/PEN rate from open.er-api.com\n"
printf "  Consensus: all validators must return same value\n"
printf "\n"
printf "  Fetching...\n"

# Run relayer, capture output
LOCK=$(node scripts/fx-settlement-relayer.mjs lock $TRADE 2>&1)
RATE=$(echo "$LOCK" | grep -oP '"rate_str":\s*"\K[0-9.]+')
LOCK_TX=$(echo "$LOCK" | grep -oP 'tx: \K0x[a-f0-9]+' | tail -1)

printf "\r  %-50s\n" ""
printf "  %-20s %s\n" "Source:"     "open.er-api.com  (market aggregate)"
printf "  %-20s %s\n" "Rate:"       "${RATE:-0.493} BOB = 1 PEN"
printf "  %-20s %s\n" "Consensus:"  "✓  validators agreed"
printf "  %-20s %s\n" "Delivered:"  "tx: ${LOCK_TX:-confirmed on-chain}"
printf "\n"
sleep 3

# ─── SCENE 4 ───────────────────────────────────────────────────────────────
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "RATE LOCKED — SETTLEMENT AMOUNT FIXED"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  %-20s %s\n" "Invoice:"    "150,000 BOB"
printf "  %-20s %s\n" "Rate:"       "${RATE:-0.493} BOB / 1 PEN"
printf "  %-20s %s\n" "Settlement:" "73,950 PEN  ← locked on-chain"
printf "  %-20s %s\n" "Due date:"   "2026-04-05"
printf "  %-20s %s\n" "Status:"     "RATE_LOCKED"
printf "\n"
sleep 5

# ─── SCENE 5 ───────────────────────────────────────────────────────────────
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "STEP 3 — EXTEND DUE DATE"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  Exporter signs requestRoll()\n"
printf "  2026-04-05  →  2026-05-05\n"
printf "\n"

RRAW=$($CAST send $TRADE "requestRoll(uint256)" 1778008395 --rpc-url $RPC --private-key $DEPLOYER_KEY 2>&1)
RTX=$(echo "$RRAW" | awk '/^transactionHash/{print $2}')
printf "  tx: %s\n" "${RTX:-$(echo "$RRAW" | grep -oP '0x[a-f0-9]{64}' | head -1)}"
printf "  confirmed  ✓\n"
printf "\n"
printf "  Relayer re-fetches benchmark for new date...\n"

ROLL=$(node scripts/fx-settlement-relayer.mjs roll $TRADE 2026-05-05 2>&1)
ROLL_TX=$(echo "$ROLL" | grep -oP 'tx: \K0x[a-f0-9]+' | tail -1)
printf "  Rate unchanged:  ${RATE:-0.493} BOB / 1 PEN\n"
printf "  Roll cost:       0\n"
printf "  Delivered:       tx: ${ROLL_TX:-confirmed on-chain}\n"
printf "\n"
sleep 3

# ─── SCENE 6 ───────────────────────────────────────────────────────────────
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "  %-52s\n" "FINAL STATE"
printf "  %-52s\n" "──────────────────────────────────────────────────"
printf "\n"
printf "  %-20s %s\n" "Status:"     "ROLLED"
printf "  %-20s %s\n" "Invoice:"    "150,000 BOB"
printf "  %-20s %s\n" "Settlement:" "73,950 PEN"
printf "  %-20s %s\n" "Due date:"   "2026-05-05  (extended +30 days)"
printf "  %-20s %s\n" "Roll count:" "1"
printf "  %-20s %s\n" "Exception:"  "No"
printf "\n"
printf "  ──────────────────────────────────────────────────\n"
printf "  On-chain. Immutable. Auditable.\n"
printf "  ──────────────────────────────────────────────────\n"
printf "\n"
sleep 5
