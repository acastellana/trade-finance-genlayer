#!/usr/bin/env node
/**
 * read-verdicts.mjs
 * Reads oracle contract state + BridgeSender messages, then saves verdict metadata.
 */
import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { readFileSync, writeFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT  = join(__dir, "..");

const RELAY_KEY     = readFileSync(join(ROOT, "base-sepolia/.wallets/relayer.key"), "utf8").trim();
const GL_RPC        = "https://studio.genlayer.com/api";
const BRIDGE_SENDER = "0xC94bE65Baf99590B1523db557D157fabaD2DA729";

const account = createAccount(RELAY_KEY.startsWith("0x") ? RELAY_KEY : "0x" + RELAY_KEY);
const gl = createClient({ chain: studionet, endpoint: GL_RPC, account });

// Oracle addresses discovered from tx.data.contract_address
const ORACLES = {
  "A_TIMELY":       { address: "0x1B0Dc09a8F1c99A150427357488b73eC71bD0Db8", settlement: "0x6A1231e490d4ce37c8a47234425e67A3Bac25514", caseId: "qc-coop-2026-0003" },
  "B_LATE":         { address: "0x107db91E2E8c4f0b2076B479312fF48318c28828", settlement: "0xB627dc05e9579c50C1593CCD0DFD5BE38C5c323B", caseId: "qc-coop-2026-0004" },
  "C_UNDETERMINED": { address: "0xf1dB1EEF17b48F104d6CB530784D95E551DC8682", settlement: "0x4cf051eCAcE62a44AEa4de2cCeCcb472e30577AD", caseId: "qc-coop-2026-0005" },
};

async function readOracleState(address) {
  // Try various field names the oracle might expose
  const fields = ["verdict", "verdict_reason", "case_id", "settlement_contract",
                  "guideline_version", "statement", "court_sheet_a_cid", "bridge_sender"];
  const state = {};

  for (const field of fields) {
    try {
      const val = await gl.readContract({
        address,
        functionName: field,
        args: [],
        jsonSafeReturn: true,
      });
      state[field] = val;
    } catch (_) {}
  }
  return state;
}

async function readBridgeSenderMessages() {
  const hashes = await gl.readContract({
    address: BRIDGE_SENDER,
    functionName: "get_message_hashes",
    args: [],
    jsonSafeReturn: true,
  });

  const messages = [];
  for (const h of (hashes || [])) {
    try {
      // Try different arg formats
      const msg = await gl.readContract({
        address: BRIDGE_SENDER,
        functionName: "get_message",
        args: [h],
        jsonSafeReturn: true,
      });
      messages.push({ hash: h, data: msg });
    } catch (e) {
      messages.push({ hash: h, error: e.message });
    }
  }
  return messages;
}

async function main() {
  const verdictMeta = {};

  console.log("=== Reading Oracle Contract States ===\n");
  for (const [label, info] of Object.entries(ORACLES)) {
    console.log(`--- ${label} (${info.address}) ---`);
    const state = await readOracleState(info.address);
    console.log("  verdict:", state.verdict ?? "(null)");
    console.log("  verdict_reason:", (state.verdict_reason ?? "(null)").toString().slice(0, 120));
    console.log("  case_id:", state.case_id ?? "(null)");
    console.log("  settlement_contract:", state.settlement_contract ?? "(null)");
    console.log("  Full state keys:", Object.keys(state).filter(k => state[k] != null));
    console.log();

    verdictMeta[info.settlement.toLowerCase()] = {
      caseId: info.caseId,
      oracleAddress: info.address,
      verdict: state.verdict || null,
      reason: state.verdict_reason || null,
      timestamp: Math.floor(Date.now() / 1000),
    };
  }

  // Save updated verdicts
  const existing = JSON.parse(readFileSync(join(ROOT, "artifacts/relay-state/genlayer-verdicts.json"), "utf8"));
  const merged = { ...existing };
  for (const [k, v] of Object.entries(verdictMeta)) {
    merged[k] = { ...existing[k], ...v };
  }
  writeFileSync(join(ROOT, "artifacts/relay-state/genlayer-verdicts.json"), JSON.stringify(merged, null, 2));
  console.log("✅ Saved updated verdicts to artifacts/relay-state/genlayer-verdicts.json\n");

  console.log("=== BridgeSender Pending Messages (sample of first 5) ===\n");
  const msgs = await readBridgeSenderMessages();
  console.log(`Total messages: ${msgs.length}`);
  msgs.slice(0, 5).forEach(m => {
    console.log(`  hash: ${m.hash}`);
    console.log(`  data: ${JSON.stringify(m.data ?? m.error).slice(0, 200)}`);
    console.log();
  });
}

main().catch(e => { console.error("Fatal:", e); process.exit(1); });
