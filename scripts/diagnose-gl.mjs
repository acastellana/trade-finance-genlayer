#!/usr/bin/env node
/**
 * diagnose-gl.mjs
 * Reads GenLayer tx data + BridgeSender state to diagnose why no messages are pending.
 */
import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT  = join(__dir, "..");

const RELAY_KEY     = readFileSync(join(ROOT, "base-sepolia/.wallets/relayer.key"), "utf8").trim();
const GL_RPC        = "https://studio.genlayer.com/api";
const BRIDGE_SENDER = "0xC94bE65Baf99590B1523db557D157fabaD2DA729";

const account = createAccount(RELAY_KEY.startsWith("0x") ? RELAY_KEY : "0x" + RELAY_KEY);
const gl = createClient({ chain: studionet, endpoint: GL_RPC, account });

const TX_HASHES = {
  "A_TIMELY":        "0xbb43cb860ed99f65d4428beb16753ef6b86ff4096bcdec5c8ac6a0744797fd0b",
  "B_LATE":          "0xf6fc46f6f24f6a1063cd27155bd8a6c9ea963aed89d0f37730bc05b66a1b9be0",
  "C_UNDETERMINED":  "0xcb53f74069ed63bdd3f8669cac66db5b41795efbadfb9e35481d14466e1be56a",
};

async function main() {
  console.log("=== GenLayer Transaction Diagnostics ===\n");

  // 1. Inspect each oracle deployment tx
  for (const [label, txHash] of Object.entries(TX_HASHES)) {
    console.log(`--- ${label}: ${txHash} ---`);
    try {
      const tx = await gl.getTransaction({ hash: txHash });
      // Print ALL fields
      console.log("  statusName:", tx.statusName);
      console.log("  resultName:", tx.resultName);

      // Look for contract address in various fields
      const raw = JSON.stringify(tx);
      console.log("  Raw keys:", Object.keys(tx).join(", "));

      // Check for contract_address or to_address in data
      if (tx.data) {
        if (typeof tx.data === "object") {
          console.log("  data keys:", Object.keys(tx.data).join(", "));
          console.log("  data.contract_address:", tx.data.contract_address);
          console.log("  data.to_address:", tx.data.to_address);
          console.log("  data.result:", JSON.stringify(tx.data.result)?.slice(0, 200));
        }
      }
      // Any field that looks like an address
      for (const [k, v] of Object.entries(tx)) {
        if (typeof v === "string" && v.startsWith("0x") && v.length === 42) {
          console.log(`  ${k} (address): ${v}`);
        }
      }
    } catch (e) {
      console.error("  Error:", e.message);
    }
    console.log();
  }

  // 2. Read BridgeSender state
  console.log("=== BridgeSender State ===");
  try {
    const hashes = await gl.readContract({
      address: BRIDGE_SENDER,
      functionName: "get_message_hashes",
      args: [],
    });
    console.log("Pending message hashes:", hashes);

    if (Array.isArray(hashes) && hashes.length > 0) {
      for (const h of hashes) {
        const msg = await gl.readContract({
          address: BRIDGE_SENDER,
          functionName: "get_message",
          args: [h],
        });
        console.log(`  Message ${h}:`, JSON.stringify(msg));
      }
    }
  } catch (e) {
    console.error("Error reading BridgeSender:", e.message);
  }

  // 3. Try reading oracle state for each scenario
  // First get oracle addresses via eth_getTransactionReceipt
  console.log("\n=== Oracle Contract States ===");
  for (const [label, txHash] of Object.entries(TX_HASHES)) {
    const rec = await fetch(GL_RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_getTransactionReceipt", params: [txHash] }),
    }).then(r => r.json());

    const receipt = rec?.result || {};
    const contractAddr = receipt.contractAddress;
    const toAddr = receipt.to;

    console.log(`\n${label}:`);
    console.log(`  receipt.contractAddress: ${contractAddr}`);
    console.log(`  receipt.to: ${toAddr}`);

    // If no contractAddress from receipt, try reading via gen_call logs
    // Also check the "to" address (factory) if contractAddress is null
    if (!contractAddr && toAddr) {
      console.log(`  -> deployed through factory ${toAddr}`);
      // Try to read contract state at the "to" address (probably factory, not court)
    }
  }
}

main().catch(e => { console.error("Fatal:", e); process.exit(1); });
