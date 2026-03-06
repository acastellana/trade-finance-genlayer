#!/usr/bin/env node
/**
 * fx-settlement-relayer.mjs
 *
 * Bridges TradeFxSettlement (Base Sepolia) ↔ FxBenchmarkOracle (GenLayer Studionet).
 *
 * What it does:
 *   1. Polls Base Sepolia for RateLockRequested and RollRequested events
 *      emitted by a TradeFxSettlement contract.
 *   2. Calls FxBenchmarkOracle on GenLayer to fetch the benchmark rate
 *      (5-validator strict_eq consensus, ~100s).
 *   3. Delivers the result back to TradeFxSettlement on Base Sepolia:
 *      - receiveRate()       for rate locks
 *      - receiveRolledRate() for rolls
 *
 * Benchmark hierarchy (matches FxBenchmarkOracle.py):
 *   Primary:  BCRP × BCB cross (PEN/USD official / 6.96 BOB/USD peg)
 *   Fallback: open.er-api aggregated market data
 *   Fail:     no lock if both unavailable; relayer logs and retries next cycle
 *
 * Usage:
 *   # Watch a specific trade contract:
 *   node scripts/fx-settlement-relayer.mjs watch <tradeAddress>
 *
 *   # Manually trigger a rate lock (for testing):
 *   node scripts/fx-settlement-relayer.mjs lock <tradeAddress>
 *
 *   # Manually trigger a roll:
 *   node scripts/fx-settlement-relayer.mjs roll <tradeAddress> <newDueDate>
 *     e.g. newDueDate = "2026-05-06"
 *
 *   # Show last known state of a trade:
 *   node scripts/fx-settlement-relayer.mjs status <tradeAddress>
 *
 * Required env vars:
 *   GL_PRIVATE_KEY        GenLayer relayer private key
 *   RELAYER_KEY           Base Sepolia relayer private key (hex, 0x-prefixed)
 *   GL_ORACLE_ADDRESS     FxBenchmarkOracle contract address on GenLayer Studionet
 *   TRADE_ADDRESS         TradeFxSettlement contract address on Base Sepolia
 *                         (can also be passed as CLI arg)
 *
 * Optional:
 *   BASE_SEPOLIA_RPC      Default: https://sepolia.base.org
 *   POLL_INTERVAL_MS      Default: 30000 (30s)
 *   FROM_BLOCK            Default: 0 (scan from genesis — use recent block for speed)
 *   STATE_FILE            Default: artifacts/relayer-state.json
 */

import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Config ──────────────────────────────────────────────────────────────────

const BASE_RPC         = process.env.BASE_SEPOLIA_RPC  || "https://sepolia.base.org";
const GL_RPC           = "https://studio.genlayer.com/api";
const CAST             = process.env.CAST_PATH          || `${process.env.HOME}/.foundry/bin/cast`;
const POLL_MS          = parseInt(process.env.POLL_INTERVAL_MS || "30000", 10);
const FROM_BLOCK       = process.env.FROM_BLOCK         || "0";
const STATE_FILE       = process.env.STATE_FILE
  || path.join(__dirname, "../artifacts/relayer-state.json");

const GL_PK            = process.env.GL_PRIVATE_KEY;
const RELAYER_PK       = process.env.RELAYER_KEY;        // Base Sepolia signing key
const GL_ORACLE        = process.env.GL_ORACLE_ADDRESS;

// ─── Event topic hashes (keccak256 of the canonical event signatures) ────────
// Pre-computed from TradeFxSettlement.sol events.

const TOPICS = {
  RateLockRequested: "0xe082272b702e2eab6b62fabd7a1ad7ab9e60dcd6c3346f51916b1a8248eac127",
  RollRequested:     "0xbdd365a79d2543b27ac131514a83756b481d72eab6d788a4da1f6a59b2c49124",
};

// ─── Logging ─────────────────────────────────────────────────────────────────

function ts() {
  return new Date().toISOString().replace("T", " ").split(".")[0];
}

function log(step, msg, obj) {
  const prefix = `[${ts()}] [${step}]`;
  if (obj !== undefined) {
    console.log(`${prefix} ${msg}`, typeof obj === "string" ? obj : JSON.stringify(obj, null, 2));
  } else {
    console.log(`${prefix} ${msg}`);
  }
}

function warn(step, msg, obj) {
  process.stderr.write(`[${ts()}] [${step}] ⚠️  ${msg}${obj ? " " + JSON.stringify(obj) : ""}\n`);
}

function err(step, msg, e) {
  process.stderr.write(`[${ts()}] [${step}] ❌ ${msg}: ${e?.message || e}\n`);
}

// ─── State management ────────────────────────────────────────────────────────
// Tracks last scanned block + set of processed event IDs to avoid double-delivery.

function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    }
  } catch (_) {}
  return { lastBlock: parseInt(FROM_BLOCK, 10), processed: [] };
}

function saveState(state) {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

function eventId(log_) {
  return `${log_.transactionHash}-${log_.logIndex}`;
}

// ─── Encoding helpers ─────────────────────────────────────────────────────────

/**
 * Convert a short ASCII string to a right-padded bytes32 hex value.
 * e.g. "BCRP_BCB_CROSS" → "0x425243505f4243425f43524f5353000...000"
 * This matches Solidity's bytes32 ABI encoding for short strings.
 */
function strToBytes32(str) {
  const hex = Buffer.from(str, "utf8").toString("hex");
  if (hex.length > 64) throw new Error(`String too long for bytes32: ${str}`);
  return "0x" + hex.padEnd(64, "0");
}

/**
 * Parse ISO 8601 UTC string to unix timestamp (seconds).
 * e.g. "2026-03-06T18:30:00Z" → 1741286400
 */
function isoToUnix(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

// ─── Base Sepolia: JSON-RPC helpers ──────────────────────────────────────────

async function rpcBase(method, params) {
  const res = await fetch(BASE_RPC, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ jsonrpc: "2.0", method, params, id: Date.now() }),
  });
  const j = await res.json();
  if (j.error) throw new Error(`Base Sepolia RPC error (${method}): ${JSON.stringify(j.error)}`);
  return j.result;
}

async function getLatestBlock() {
  const hex = await rpcBase("eth_blockNumber", []);
  return parseInt(hex, 16);
}

/**
 * Fetch all logs matching the given topic from a contract address,
 * between fromBlock and toBlock.
 */
async function getLogs(contractAddress, topic0, fromBlock, toBlock) {
  return rpcBase("eth_getLogs", [{
    address:   contractAddress,
    topics:    [topic0],
    fromBlock: "0x" + fromBlock.toString(16),
    toBlock:   "0x" + toBlock.toString(16),
  }]);
}

/**
 * Read the current status enum from TradeFxSettlement.getSummary().
 * Returns an object with parsed fields.
 */
async function readTradeSummary(tradeAddress) {
  // getSummary() returns (Status, uint256, uint256, uint256, uint256, uint256, bool)
  // ABI-encode the call using cast
  const data = execSync(
    `${CAST} calldata "getSummary()"`,
    { encoding: "utf8" }
  ).trim();

  const raw = await rpcBase("eth_call", [
    { to: tradeAddress, data },
    "latest",
  ]);

  // Decode with cast
  const decoded = execSync(
    `${CAST} abi-decode "getSummary()(uint8,uint256,uint256,uint256,uint256,uint256,bool)" ${raw}`,
    { encoding: "utf8" }
  ).trim();

  const lines = decoded.split("\n").map(l => l.trim());
  return {
    status:           parseInt(lines[0], 10),
    invoiceAmount:    lines[1],
    currentNotional:  lines[2],
    settlementAmount: lines[3],
    currentDueDate:   parseInt(lines[4], 10),
    rollCount:        parseInt(lines[5], 10),
    exceptionFlagged: lines[6] === "true",
  };
}

/**
 * Call TradeFxSettlement.receiveRate() on Base Sepolia via cast send.
 * @param {string}  tradeAddress  Contract address
 * @param {bigint}  rate18        Rate × 10^18
 * @param {string}  benchmarkType Short string, e.g. "BCRP_BCB_CROSS"
 * @param {string}  benchmarkId   Short string, e.g. "BCRP-BCB-20260306"
 * @param {string}  asOfIso       ISO 8601 UTC timestamp
 */
function sendReceiveRate(tradeAddress, rate18, benchmarkType, benchmarkId, asOfIso) {
  const btBytes32   = strToBytes32(benchmarkType);
  const bidBytes32  = strToBytes32(benchmarkId);
  const asOfUnix    = isoToUnix(asOfIso);

  log("relayer", `→ receiveRate(${rate18}, "${benchmarkType}", "${benchmarkId}", ${asOfUnix})`);

  const cmd = [
    CAST, "send", tradeAddress,
    '"receiveRate(uint256,bytes32,bytes32,uint256)"',
    rate18.toString(),
    btBytes32,
    bidBytes32,
    asOfUnix.toString(),
    "--rpc-url", BASE_RPC,
    "--private-key", RELAYER_PK,
  ].join(" ");

  const out = execSync(cmd, { encoding: "utf8" });
  const txHash = out.match(/transactionHash\s+(0x[0-9a-f]+)/i)?.[1] || "?";
  log("relayer", `✅ receiveRate delivered. tx: ${txHash}`);
  return txHash;
}

/**
 * Call TradeFxSettlement.receiveRolledRate() on Base Sepolia via cast send.
 * roll_cost = 0 (spot re-lock, no OTC forward points available for BOB/PEN).
 */
function sendReceiveRolledRate(tradeAddress, rate18, benchmarkId, asOfIso) {
  // Note: newDueDate is NOT a parameter here — the contract reads _pendingNewDueDate
  // from storage (set by requestRoll()). Only 4 parameters.
  const bidBytes32 = strToBytes32(benchmarkId);
  const asOfUnix   = isoToUnix(asOfIso);
  const rollCost   = 0;  // spot re-lock

  log("relayer", `→ receiveRolledRate(${rate18}, rollCost=0, "${benchmarkId}", ${asOfUnix})`);

  const cmd = [
    CAST, "send", tradeAddress,
    '"receiveRolledRate(uint256,uint256,bytes32,uint256)"',
    rate18.toString(),
    rollCost.toString(),
    bidBytes32,
    asOfUnix.toString(),
    "--rpc-url", BASE_RPC,
    "--private-key", RELAYER_PK,
  ].join(" ");

  const out = execSync(cmd, { encoding: "utf8" });
  const txHash = out.match(/transactionHash\s+(0x[0-9a-f]+)/i)?.[1] || "?";
  log("relayer", `✅ receiveRolledRate delivered. tx: ${txHash}`);
  return txHash;
}

// ─── GenLayer: oracle interaction ────────────────────────────────────────────

function makeGLClient() {
  if (!GL_PK) throw new Error("GL_PRIVATE_KEY not set");
  const account = createAccount(GL_PK);
  return createClient({ chain: studionet, account });
}

async function glFund(address) {
  try {
    await fetch(`${GL_RPC}/faucet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
  } catch (_) {}
}

async function glWaitAccepted(client, hash, label) {
  log("genlayer", `⏳ Waiting for ${label} (${hash.slice(0, 10)}…)`);
  return client.waitForTransactionReceipt({
    hash,
    status:   TransactionStatus.ACCEPTED,
    retries:  180,
    interval: 5000,
  });
}

/**
 * Call request_rate_lock(tradeAddress) on the GenLayer oracle.
 * Blocks until consensus (~100s). Returns the parsed benchmark result.
 */
async function glRequestRateLock(tradeAddress) {
  const client = makeGLClient();
  const account = createAccount(GL_PK);
  await glFund(account.address);
  await client.initializeConsensusSmartContract();

  // Try PRIMARY benchmark first (BCRP×BCB). If it fails consensus, fall back.
  let fn = "request_rate_lock_primary";
  log("genlayer", `${fn}(${tradeAddress})`);
  let hash = await client.writeContract({
    address:      GL_ORACLE,
    functionName: fn,
    args:         [tradeAddress],
    leaderOnly:   false,
  });
  log("genlayer", `TX: ${hash}`);
  let receipt = await glWaitAccepted(client, hash, fn);
  let ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;

  if (!ok) {
    const leader = receipt.consensus_data?.leader_receipt?.[0];
    warn("genlayer", `${fn} failed: ${receipt.result_name}. Falling back. stderr=${(leader?.genvm_result?.stderr || "").slice(0, 120)}`);

    fn = "request_rate_lock_fallback";
    log("genlayer", `${fn}(${tradeAddress})`);
    hash = await client.writeContract({
      address:      GL_ORACLE,
      functionName: fn,
      args:         [tradeAddress],
      leaderOnly:   false,
    });
    log("genlayer", `TX: ${hash}`);

    receipt = await glWaitAccepted(client, hash, fn);
    ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;
    if (!ok) {
      const leader2 = receipt.consensus_data?.leader_receipt?.[0];
      throw new Error(`${fn} failed: ${receipt.result_name} | stderr=${(leader2?.genvm_result?.stderr || "").slice(0, 200)}`);
    }
  }

  // Read back the result
  log("genlayer", `get_locked_rate(${tradeAddress})`);
  const raw = await client.readContract({
    address:      GL_ORACLE,
    functionName: "get_locked_rate",
    args:         [tradeAddress],
  });

  const result = typeof raw === "string" ? JSON.parse(raw) : raw;
  log("genlayer", "Benchmark result:", result);
  return result;
}

/**
 * Call request_roll(tradeAddress, newDueDate) on the GenLayer oracle.
 * Blocks until consensus. Returns the parsed benchmark result.
 */
async function glRequestRoll(tradeAddress, newDueDate) {
  const client = makeGLClient();
  const account = createAccount(GL_PK);
  await glFund(account.address);
  await client.initializeConsensusSmartContract();

  let fn = "request_roll_primary";
  log("genlayer", `${fn}(${tradeAddress}, ${newDueDate})`);
  let hash = await client.writeContract({
    address:      GL_ORACLE,
    functionName: fn,
    args:         [tradeAddress, newDueDate],
    leaderOnly:   false,
  });
  log("genlayer", `TX: ${hash}`);
  let receipt = await glWaitAccepted(client, hash, fn);
  let ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;

  if (!ok) {
    const leader = receipt.consensus_data?.leader_receipt?.[0];
    warn("genlayer", `${fn} failed: ${receipt.result_name}. Falling back. stderr=${(leader?.genvm_result?.stderr || "").slice(0, 120)}`);

    fn = "request_roll_fallback";
    log("genlayer", `${fn}(${tradeAddress}, ${newDueDate})`);
    hash = await client.writeContract({
      address:      GL_ORACLE,
      functionName: fn,
      args:         [tradeAddress, newDueDate],
      leaderOnly:   false,
    });
    log("genlayer", `TX: ${hash}`);
    receipt = await glWaitAccepted(client, hash, fn);
    ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;
    if (!ok) {
      const leader2 = receipt.consensus_data?.leader_receipt?.[0];
      throw new Error(`${fn} failed: ${receipt.result_name} | stderr=${(leader2?.genvm_result?.stderr || "").slice(0, 200)}`);
    }
  }

  log("genlayer", `get_rolled_rate(${tradeAddress})`);
  const raw = await client.readContract({
    address:      GL_ORACLE,
    functionName: "get_rolled_rate",
    args:         [tradeAddress],
  });

  const result = typeof raw === "string" ? JSON.parse(raw) : raw;
  log("genlayer", "Roll benchmark result:", result);
  return result;
}

// ─── Event handlers ──────────────────────────────────────────────────────────

/**
 * Handle a RateLockRequested event.
 * Fetches benchmark from GenLayer, delivers receiveRate() to Base Sepolia.
 */
async function handleRateLockRequested(tradeAddress, eventLog) {
  log("handler", `RateLockRequested from ${tradeAddress}`);

  let result;
  try {
    result = await glRequestRateLock(tradeAddress);
  } catch (e) {
    err("handler", "GenLayer request_rate_lock failed", e);
    return false;  // don't mark as processed — will retry next cycle
  }

  try {
    sendReceiveRate(
      tradeAddress,
      BigInt(result.rate_18),
      result.benchmark_type,
      result.benchmark_id,
      result.as_of_timestamp,
    );
    return true;
  } catch (e) {
    err("handler", "Base Sepolia receiveRate failed", e);
    return false;
  }
}

/**
 * Handle a RollRequested event.
 * The requestedNewDueDate is encoded in the event log data (3rd non-indexed uint256).
 * Fetches new benchmark from GenLayer, delivers receiveRolledRate() to Base Sepolia.
 */
async function handleRollRequested(tradeAddress, eventLog) {
  // RollRequested(address indexed requester, uint256 currentDueDate,
  //               uint256 requestedNewDueDate, uint256 timestamp)
  // topics[0] = event sig, topics[1] = requester (indexed)
  // data contains: currentDueDate, requestedNewDueDate, timestamp (each 32 bytes)
  const data = eventLog.data.slice(2);  // strip 0x
  const currentDueDate      = parseInt(data.slice(0,   64), 16);   // not needed
  const requestedNewDueSecs = parseInt(data.slice(64, 128), 16);
  const newDueDate = new Date(requestedNewDueSecs * 1000).toISOString().split("T")[0];

  log("handler", `RollRequested from ${tradeAddress}: newDueDate=${newDueDate}`);

  let result;
  try {
    result = await glRequestRoll(tradeAddress, newDueDate);
  } catch (e) {
    err("handler", "GenLayer request_roll failed", e);
    return false;
  }

  try {
    sendReceiveRolledRate(
      tradeAddress,
      BigInt(result.rate_18),
      result.benchmark_id,
      result.as_of_timestamp,
    );
    return true;
  } catch (e) {
    err("handler", "Base Sepolia receiveRolledRate failed", e);
    return false;
  }
}

// ─── Main polling loop ────────────────────────────────────────────────────────

async function poll(tradeAddress) {
  const state = loadState();
  const latestBlock = await getLatestBlock();

  if (state.lastBlock >= latestBlock) {
    log("poll", `No new blocks (lastBlock=${state.lastBlock})`);
    return;
  }

  log("poll", `Scanning blocks ${state.lastBlock + 1}–${latestBlock} on ${tradeAddress}`);

  // Fetch both event types in parallel
  const [lockLogs, rollLogs] = await Promise.all([
    getLogs(tradeAddress, TOPICS.RateLockRequested, state.lastBlock + 1, latestBlock),
    getLogs(tradeAddress, TOPICS.RollRequested,     state.lastBlock + 1, latestBlock),
  ]);

  const allEvents = [
    ...lockLogs.map(l => ({ ...l, kind: "lock" })),
    ...rollLogs.map(l => ({ ...l, kind: "roll" })),
  ].sort((a, b) => parseInt(a.blockNumber, 16) - parseInt(b.blockNumber, 16));

  log("poll", `Found ${lockLogs.length} RateLockRequested + ${rollLogs.length} RollRequested events`);

  for (const event of allEvents) {
    const id = eventId(event);
    if (state.processed.includes(id)) {
      log("poll", `Skip already-processed event ${id}`);
      continue;
    }

    let ok = false;
    if (event.kind === "lock") {
      ok = await handleRateLockRequested(tradeAddress, event);
    } else if (event.kind === "roll") {
      ok = await handleRollRequested(tradeAddress, event);
    }

    if (ok) {
      state.processed.push(id);
      // Cap processed list at 1000 entries to avoid unbounded growth
      if (state.processed.length > 1000) state.processed = state.processed.slice(-500);
      saveState(state);
    }
  }

  state.lastBlock = latestBlock;
  saveState(state);
  log("poll", `Done. lastBlock=${latestBlock}`);
}

// ─── Commands ────────────────────────────────────────────────────────────────

async function cmdWatch(tradeAddress) {
  log("main", `Watching ${tradeAddress} | GL oracle: ${GL_ORACLE} | poll: ${POLL_MS}ms`);
  while (true) {
    try {
      await poll(tradeAddress);
    } catch (e) {
      err("poll", "Unhandled error in poll cycle", e);
    }
    await new Promise(r => setTimeout(r, POLL_MS));
  }
}

async function cmdLock(tradeAddress) {
  log("main", `Manual rate lock for ${tradeAddress}`);
  const result = await glRequestRateLock(tradeAddress);
  sendReceiveRate(
    tradeAddress,
    BigInt(result.rate_18),
    result.benchmark_type,
    result.benchmark_id,
    result.as_of_timestamp,
  );
}

async function cmdRoll(tradeAddress, newDueDate) {
  log("main", `Manual roll for ${tradeAddress} → ${newDueDate}`);
  const result = await glRequestRoll(tradeAddress, newDueDate);
  sendReceiveRolledRate(
    tradeAddress,
    BigInt(result.rate_18),
    result.benchmark_id,
    result.as_of_timestamp,
    newDueDate,
  );
}

async function cmdStatus(tradeAddress) {
  const STATUS_NAMES = [
    "DRAFT", "RATE_PENDING", "RATE_LOCKED", "PARTIAL_RESIZED",
    "ROLL_PENDING", "ROLLED", "SETTLED", "CANCELLED",
  ];
  const summary = await readTradeSummary(tradeAddress);
  const statusName = STATUS_NAMES[summary.status] || `UNKNOWN(${summary.status})`;
  console.log("\n┌─────────────────────────────────────────────┐");
  console.log(`│  Trade:          ${tradeAddress.slice(0, 22)}…   │`);
  console.log(`│  Status:         ${statusName.padEnd(27)}│`);
  console.log(`│  Invoice amount: ${summary.invoiceAmount.slice(0, 27).padEnd(27)}│`);
  console.log(`│  Notional:       ${summary.currentNotional.slice(0, 27).padEnd(27)}│`);
  console.log(`│  Settlement amt: ${summary.settlementAmount.slice(0, 27).padEnd(27)}│`);
  console.log(`│  Due date:       ${new Date(summary.currentDueDate * 1000).toISOString().split("T")[0].padEnd(27)}│`);
  console.log(`│  Roll count:     ${summary.rollCount.toString().padEnd(27)}│`);
  console.log(`│  Exception:      ${(summary.exceptionFlagged ? "YES ⚠️" : "No").padEnd(27)}│`);
  console.log("└─────────────────────────────────────────────┘\n");
}

// ─── Entrypoint ───────────────────────────────────────────────────────────────

const [,, command, arg1, arg2] = process.argv;
const tradeArg = arg1 || process.env.TRADE_ADDRESS;

function requireConfig(...keys) {
  for (const k of keys) {
    if (!process.env[k]) throw new Error(`Missing env var: ${k}`);
  }
}

(async () => {
  try {
    switch (command) {
      case "watch":
        requireConfig("GL_PRIVATE_KEY", "RELAYER_KEY", "GL_ORACLE_ADDRESS");
        if (!tradeArg) throw new Error("Usage: watch <tradeAddress>");
        await cmdWatch(tradeArg);
        break;

      case "lock":
        requireConfig("GL_PRIVATE_KEY", "RELAYER_KEY", "GL_ORACLE_ADDRESS");
        if (!tradeArg) throw new Error("Usage: lock <tradeAddress>");
        await cmdLock(tradeArg);
        break;

      case "roll":
        requireConfig("GL_PRIVATE_KEY", "RELAYER_KEY", "GL_ORACLE_ADDRESS");
        if (!tradeArg || !arg2) throw new Error("Usage: roll <tradeAddress> <newDueDate YYYY-MM-DD>");
        await cmdRoll(tradeArg, arg2);
        break;

      case "status":
        if (!tradeArg) throw new Error("Usage: status <tradeAddress>");
        await cmdStatus(tradeArg);
        break;

      default:
        console.log(`
fx-settlement-relayer.mjs — TradeFxSettlement ↔ FxBenchmarkOracle bridge

Commands:
  watch  <tradeAddress>                   Poll for events and auto-deliver rates
  lock   <tradeAddress>                   Manually trigger a rate lock
  roll   <tradeAddress> <YYYY-MM-DD>      Manually trigger a hedge roll
  status <tradeAddress>                   Show current trade state

Required env vars:
  GL_PRIVATE_KEY       GenLayer relayer key
  RELAYER_KEY          Base Sepolia relayer key (0x-prefixed)
  GL_ORACLE_ADDRESS    FxBenchmarkOracle on GenLayer Studionet
  TRADE_ADDRESS        TradeFxSettlement on Base Sepolia (or pass as arg)

Optional:
  BASE_SEPOLIA_RPC     Default: https://sepolia.base.org
  POLL_INTERVAL_MS     Default: 30000
  FROM_BLOCK           Default: 0
`);
    }
  } catch (e) {
    err("main", "Fatal", e);
    process.exit(1);
  }
})();
