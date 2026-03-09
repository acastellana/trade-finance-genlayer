#!/usr/bin/env node
/**
 * conditional-payment-relay.mjs
 *
 * Bridge relay for TradeFxSettlement <-> GenLayer.
 *
 * Direction 1 — Base -> GenLayer (EvmToGl):
 *   Polls watched TradeFxSettlement contracts for ShipmentContested events.
 *   On each new contest:
 *     1. Fetches manifest.json from IPFS to get court_sheet_a_cid + court_sheet_b_cid
 *     2. Deploys ShipmentDeadlineCourt.py on GenLayer with those CIDs
 *     3. Waits for AI jury finalization (~100-200s)
 *     4. Reads verdict + stores oracle metadata
 *
 * Direction 2 — GenLayer -> Base (GlToEvm):
 *   Polls BridgeSender.py on GenLayer for pending messages.
 *   For each message:
 *     1. Quotes fee on BridgeForwarder (zkSync Sepolia)
 *     2. Calls callRemoteArbitrary → LayerZero → BridgeReceiver on Base
 *     3. BridgeReceiver calls processBridgeMessage() on TradeFxSettlement
 *
 * Usage:
 *   node scripts/conditional-payment-relay.mjs
 *   node scripts/conditional-payment-relay.mjs --once   (one-shot, no loop)
 *
 * Env vars (from .relay.env or environment):
 *   RELAY_PRIVATE_KEY       Relayer wallet key (hex, 0x-prefixed)
 *   BASE_RPC_URL            Base Sepolia RPC
 *   ZKSYNC_RPC_URL          zkSync Sepolia RPC
 *   GENLAYER_RPC_URL        GenLayer Studionet RPC
 *   CONTRACTS               Comma-separated TradeFxSettlement addresses to watch
 */

import { ethers } from "ethers";
import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __dir  = dirname(fileURLToPath(import.meta.url));
const ROOT   = join(__dir, "..");
const DATA   = join(ROOT, "artifacts", "relay-state");
const ONCE   = process.argv.includes("--once");

// ── Config ────────────────────────────────────────────────────────────────────

function loadEnv() {
  const envPath = join(ROOT, ".relay.env");
  if (existsSync(envPath)) {
    readFileSync(envPath, "utf8").split("\n").forEach(line => {
      const [k, ...v] = line.split("=");
      if (k && !process.env[k.trim()]) process.env[k.trim()] = v.join("=").trim();
    });
  }
}
loadEnv();

const RELAY_KEY      = process.env.RELAY_PRIVATE_KEY || readFileSync(join(ROOT, "base-sepolia/.wallets/relayer.key"), "utf8").trim();
const BASE_RPC       = process.env.BASE_RPC_URL       || "https://sepolia.base.org";
const ZKSYNC_RPC     = process.env.ZKSYNC_RPC_URL     || "https://sepolia.era.zksync.dev";
const GL_RPC         = process.env.GENLAYER_RPC_URL    || "https://studio.genlayer.com/api";
const IPFS_GATEWAY   = "https://ipfs.io/ipfs/";

// InternetCourt bridge contracts (already deployed)
const BRIDGE_SENDER    = "0xC94bE65Baf99590B1523db557D157fabaD2DA729"; // GenLayer
const BRIDGE_FORWARDER = "0x95c4E5b042d75528f7df355742e48B298028b3f2"; // zkSync Sepolia
const LZ_DST_EID       = 40245; // Base Sepolia

// Contracts to watch — from v5-scenarios.json or env
let WATCHED_CONTRACTS = [];
if (process.env.CONTRACTS) {
  WATCHED_CONTRACTS = process.env.CONTRACTS.split(",").map(s => s.trim());
} else {
  try {
    const scenarios = JSON.parse(readFileSync(join(ROOT, "artifacts/v5-scenarios.json"), "utf8"));
    WATCHED_CONTRACTS = scenarios.map(s => s.contract);
  } catch (_) {}
}

if (!WATCHED_CONTRACTS.length) {
  console.error("❌ No contracts to watch. Set CONTRACTS env or ensure artifacts/v5-scenarios.json exists.");
  process.exit(1);
}

console.log(`Watching ${WATCHED_CONTRACTS.length} contract(s):`);
WATCHED_CONTRACTS.forEach(a => console.log(`  ${a}`));

// ── Providers + clients ───────────────────────────────────────────────────────

const baseProvider   = new ethers.JsonRpcProvider(BASE_RPC);
const zksyncProvider = new ethers.JsonRpcProvider(ZKSYNC_RPC);
const relayWallet    = new ethers.Wallet(RELAY_KEY.startsWith("0x") ? RELAY_KEY : "0x" + RELAY_KEY);
const baseSigner     = relayWallet.connect(baseProvider);
const zksyncSigner   = relayWallet.connect(zksyncProvider);

const glAccount = createAccount((RELAY_KEY.startsWith("0x") ? RELAY_KEY : "0x" + RELAY_KEY) );
const glClient  = createClient({ chain: studionet, endpoint: GL_RPC, account: glAccount });

// ── ABIs ──────────────────────────────────────────────────────────────────────

const TFX_ABI = [
  "event ShipmentContested(address indexed contestant, string manifestCid, string statement, uint256 contestDeadline, uint256 timestamp)",
  "function shipmentStatus() view returns (uint8)",
  "function shipmentManifestCid() view returns (string)",
  "function shipmentStatement() view returns (string)",
  "function shipmentGuidelineVersion() view returns (string)",
];

const BRIDGE_SENDER_ABI = [
  "function get_message_hashes() view returns (string[])",
  "function get_message(string hash) view returns (tuple(uint256 target_chain_id, string target_contract, bytes data))",
  "function delete_message(string hash)",
];

const BRIDGE_FORWARDER_ABI = [
  "function quoteCallRemoteArbitrary(uint32 dstEid, bytes data, bytes options) view returns (uint256 nativeFee, uint256 lzTokenFee)",
  "function callRemoteArbitrary(bytes32 txHash, uint32 dstEid, bytes data, bytes options) payable",
  "function isHashUsed(bytes32 txHash) view returns (bool)",
];

// ── State persistence ─────────────────────────────────────────────────────────

mkdirSync(DATA, { recursive: true });
const PROCESSED_FILE = join(DATA, "processed-contests.json");
const GL_META_FILE   = join(DATA, "genlayer-verdicts.json");

function loadJson(path, def) {
  try { return JSON.parse(readFileSync(path, "utf8")); } catch { return def; }
}
function saveJson(path, data) {
  writeFileSync(path + ".tmp", JSON.stringify(data, null, 2));
  // rename is atomic
  writeFileSync(path, JSON.stringify(data, null, 2));
}

// ── DIRECTION 1: Base → GenLayer ──────────────────────────────────────────────

async function pollContests() {
  const processed = new Set(loadJson(PROCESSED_FILE, []));
  const currentBlock = await baseProvider.getBlockNumber();
  const lookback = currentBlock - 1000;

  for (const addr of WATCHED_CONTRACTS) {
    const contract = new ethers.Contract(addr, TFX_ABI, baseProvider);
    const filter   = contract.filters.ShipmentContested();
    const events   = await contract.queryFilter(filter, lookback, currentBlock);

    for (const evt of events) {
      const key = `${addr.toLowerCase()}-${evt.transactionHash}`;
      if (processed.has(key)) continue;

      const log = evt;
      const { manifestCid, statement } = log.args;

      console.log(`\n[EVM→GL] ShipmentContested on ${addr}`);
      console.log(`  manifestCid: ${manifestCid}`);
      console.log(`  statement:   ${statement.slice(0, 80)}...`);

      try {
        await processContest(addr, manifestCid, statement);
        processed.add(key);
        saveJson(PROCESSED_FILE, [...processed]);
      } catch (err) {
        console.error(`[EVM→GL] Failed for ${addr}:`, err.message);
      }
    }
  }
}

async function processContest(settlementAddr, manifestCid, statement) {
  // 1. Fetch manifest from IPFS to get court sheet CIDs
  console.log(`[EVM→GL] Fetching manifest: ${manifestCid}`);
  const cleanCid  = manifestCid.replace("ipfs://", "").replace("Qm", "Qm").trim();
  const manifestUrl = IPFS_GATEWAY + cleanCid;

  let manifest;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(manifestUrl, { signal: AbortSignal.timeout(15000) });
      manifest = await res.json();
      break;
    } catch (e) {
      if (attempt === 2) throw new Error(`Failed to fetch manifest after 3 attempts: ${e.message}`);
      await sleep(3000);
    }
  }

  const sheetACid = manifest.court_inputs.court_sheet_a.cid.replace("ipfs://", "");
  const sheetBCid = manifest.court_inputs.court_sheet_b.cid.replace("ipfs://", "");
  const guidelineVersion = manifest.guideline_version || "shipment-deadline-v1";
  const caseId = manifest.case_id;

  console.log(`[EVM→GL] court_sheet_a: ${sheetACid}`);
  console.log(`[EVM→GL] court_sheet_b: ${sheetBCid}`);

  // 2. Read court contract source
  const courtSrc = readFileSync(join(ROOT, "contracts/ShipmentDeadlineCourt.py"), "utf8");

  // 3. Deploy ShipmentDeadlineCourt on GenLayer
  console.log(`[EVM→GL] Deploying ShipmentDeadlineCourt for ${caseId}...`);
  const txHash = await glClient.deployContract({
    code: courtSrc,
    args: [
      caseId,             // case_id: str
      settlementAddr,     // settlement_contract: str
      statement,          // statement: str
      guidelineVersion,   // guideline_version: str
      sheetACid,          // court_sheet_a_cid: str
      sheetBCid,          // court_sheet_b_cid: str
      BRIDGE_SENDER,      // bridge_sender: str
      LZ_DST_EID,         // target_chain_eid: int
    ],
    leaderOnly: false,
  });

  console.log(`[EVM→GL] Deploy tx: ${txHash}`);
  console.log(`[EVM→GL] GenLayer explorer: https://explorer-studio.genlayer.com/transactions/${txHash}`);
  console.log(`[EVM→GL] Waiting for AI jury consensus (~120s)...`);

  // 4. Wait for finalization
  let oracleAddress = null;
  let verdict = "";
  let reason  = "";

  for (let i = 0; i < 80; i++) {
    await sleep(5000);
    try {
      const tx = await glClient.getTransaction({ hash: txHash });
      if (tx.statusName === "FINALIZED") {
        console.log(`[EVM→GL] ✅ Oracle finalized: ${txHash}`);
        // Try to get contract state for verdict
        const rec = await glJsonRpc("gen_getTransactionReceipt", [txHash]);
        oracleAddress = rec?.data?.contract_address || rec?.contract_address || null;
        if (oracleAddress) {
          const state = await glJsonRpc("gen_getContractState", [oracleAddress]);
          verdict = state?.verdict || "";
          reason  = state?.verdict_reason || "";
          console.log(`[EVM→GL] Verdict: ${verdict} — ${reason.slice(0, 100)}`);
        }
        break;
      }
      if (["CANCELED", "UNDETERMINED"].includes(tx.statusName) ||
          ["FAILURE", "DISAGREE"].includes(tx.resultName)) {
        console.error(`[EVM→GL] Oracle failed: status=${tx.statusName} result=${tx.resultName}`);
        break;
      }
      if (i % 6 === 0) process.stdout.write(`  [${i * 5}s] status=${tx.statusName}...\n`);
    } catch (_) {}
  }

  // Save GL metadata
  const meta = loadJson(GL_META_FILE, {});
  meta[settlementAddr.toLowerCase()] = {
    caseId, oracleTxHash: txHash, oracleAddress,
    verdict, reason, timestamp: Math.floor(Date.now() / 1000)
  };
  saveJson(GL_META_FILE, meta);
  console.log(`[EVM→GL] Saved verdict metadata`);
}

// ── DIRECTION 2: GenLayer → Base (via zkSync + LayerZero) ────────────────────

async function relayVerdictsToBase() {
  console.log("\n[GL→EVM] Checking BridgeSender for pending messages...");

  // Call GenLayer BridgeSender to get pending message hashes
  const hashes = await glJsonRpc("gen_call", [{
    to: BRIDGE_SENDER,
    data: encodeFnCall("get_message_hashes", []),
  }]);

  if (!hashes || !Array.isArray(hashes) || hashes.length === 0) {
    console.log("[GL→EVM] No pending messages.");
    return;
  }

  console.log(`[GL→EVM] ${hashes.length} pending message(s) found.`);

  const forwarder = new ethers.Contract(BRIDGE_FORWARDER, BRIDGE_FORWARDER_ABI, zksyncSigner);

  for (const msgHash of hashes) {
    console.log(`\n[GL→EVM] Processing message hash: ${msgHash}`);

    // Get message details
    const msg = await glJsonRpc("gen_call", [{
      to: BRIDGE_SENDER,
      data: encodeFnCall("get_message", [msgHash]),
    }]);

    if (!msg) {
      console.warn("[GL→EVM] Could not fetch message, skipping.");
      continue;
    }

    // Check if already relayed (replay protection in BridgeForwarder)
    const txHashBytes32 = ethers.keccak256(ethers.toUtf8Bytes(msgHash));
    const isUsed = await forwarder.isHashUsed(txHashBytes32);
    if (isUsed) {
      console.log(`[GL→EVM] Already relayed: ${msgHash}`);
      continue;
    }

    try {
      // Encode data as bytes
      const dataBytes = ethers.hexlify(
        typeof msg.data === "string" ? ethers.toUtf8Bytes(msg.data) : msg.data
      );
      const options = "0x00030100110100000000000000000000000000030d40"; // LZ options: 200k gas

      // Quote fee
      const [nativeFee] = await forwarder.quoteCallRemoteArbitrary(LZ_DST_EID, dataBytes, options);
      const feeWithBuffer = nativeFee * 12n / 10n; // 20% buffer
      console.log(`[GL→EVM] LayerZero fee: ${ethers.formatEther(nativeFee)} ETH (sending with 20% buffer)`);

      // Check zkSync ETH balance
      const bal = await zksyncProvider.getBalance(relayWallet.address);
      if (bal < feeWithBuffer) {
        console.error(`[GL→EVM] Insufficient zkSync ETH: ${ethers.formatEther(bal)} < ${ethers.formatEther(feeWithBuffer)}`);
        continue;
      }

      // Send via LayerZero
      const tx = await forwarder.callRemoteArbitrary(
        txHashBytes32, LZ_DST_EID, dataBytes, options,
        { value: feeWithBuffer }
      );
      console.log(`[GL→EVM] BridgeForwarder tx (zkSync): ${tx.hash}`);
      await tx.wait();
      console.log(`[GL→EVM] ✅ Relayed via LayerZero`);

    } catch (err) {
      console.error(`[GL→EVM] Relay failed for ${msgHash}:`, err.message);
    }
  }
}

// ── GenLayer JSON-RPC helper ──────────────────────────────────────────────────

async function glJsonRpc(method, params) {
  try {
    const res = await fetch(GL_RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
      signal: AbortSignal.timeout(15000),
    });
    const data = await res.json();
    return data?.result ?? null;
  } catch { return null; }
}

function encodeFnCall(fn, args) { return JSON.stringify({ fn, args }); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Main loop ─────────────────────────────────────────────────────────────────

async function run() {
  console.log("\n🌉 Trade Finance Relay Service");
  console.log(`   Base Sepolia → GenLayer Studionet → zkSync Sepolia → LayerZero → Base Sepolia`);
  console.log(`   Relay wallet: ${relayWallet.address}`);
  console.log(`   ONCE mode: ${ONCE}\n`);

  do {
    try {
      // Direction 1: watch for new contests, deploy courts on GenLayer
      await pollContests();

      // Direction 2: relay completed verdicts back to Base
      await relayVerdictsToBase();

    } catch (err) {
      console.error("[relay] Unhandled error:", err.message);
    }

    if (!ONCE) {
      console.log("\n[relay] Sleeping 30s...\n");
      await sleep(30000);
    }
  } while (!ONCE);
}

run().catch(e => { console.error("Fatal:", e); process.exit(1); });
