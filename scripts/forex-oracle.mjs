#!/usr/bin/env node
/**
 * forex-oracle.mjs
 *
 * Deploy and/or call the ForexOracle GenLayer intelligent contract.
 * Fetches BOB/PEN from 3 independent sources; stores rate_18 for Solidity.
 *
 * Usage:
 *   node scripts/forex-oracle.mjs deploy              # Deploy fresh oracle
 *   node scripts/forex-oracle.mjs update <address>    # Trigger rate update
 *   node scripts/forex-oracle.mjs read   <address>    # Read stored rate
 *   node scripts/forex-oracle.mjs run                 # Deploy + update + read (full flow)
 *
 * The printed rate_18 value is ready to paste into:
 *   FOREX_RATE_18=<rate_18> forge script script/Deploy.s.sol ...
 */

import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONTRACT_PATH = path.join(__dirname, "../contracts/ForexOracle.py");
const STATE_FILE = path.join(__dirname, "../artifacts/forex-oracle-address.json");

const RPC = "https://studio.genlayer.com/api";
const EXPLORER = "https://explorer-studio.genlayer.com/transactions";
const PRIVATE_KEY = process.env.GL_PRIVATE_KEY || "0xdeadbeef00000000000000000000000000000000000000000000000000000001";

// --- helpers ----------------------------------------------------------------

async function rpcRaw(method, params) {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: Date.now() }),
  });
  const json = await res.json();
  if (json.error) throw new Error(`RPC error (${method}): ${JSON.stringify(json.error)}`);
  return json.result;
}

function makeClient(pk) {
  const account = createAccount(pk);
  return { client: createClient({ chain: studionet, account }), account };
}

async function waitAccepted(client, hash, label = "tx") {
  console.log(`⏳ Waiting for ${label}`);
  console.log(`   TX:      ${hash}`);
  console.log(`   Explorer: ${EXPLORER}/${hash}`);
  const receipt = await client.waitForTransactionReceipt({
    hash,
    status: TransactionStatus.ACCEPTED,
    retries: 180,
    interval: 5000,
  });
  const ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;
  if (!ok) throw new Error(`${label} failed: ${receipt.result_name} — ${JSON.stringify(receipt)}`);
  console.log(`✅ ${label} accepted`);
  return receipt;
}

async function fund(address) {
  await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", method: "sim_fundAccount",
      params: [address, 10_000_000], id: 1,
    }),
  });
}

async function readWithRetry(client, address, fn, args = []) {
  for (let i = 0; i < 6; i++) {
    try {
      return await client.readContract({ address, functionName: fn, args });
    } catch (e) {
      if (i < 5 && (e.message?.includes("not deployed") || e.message?.includes("not found"))) {
        await new Promise(r => setTimeout(r, 5000 * (i + 1)));
        continue;
      }
      throw e;
    }
  }
}

// --- actions ----------------------------------------------------------------

async function deploy() {
  console.log("🚀 Deploying ForexOracle to GenLayer Studionet…");
  const { client, account } = makeClient(PRIVATE_KEY);

  await fund(account.address);
  await client.initializeConsensusSmartContract();

  const code = fs.readFileSync(CONTRACT_PATH, "utf8");
  const hash = await client.deployContract({
    code,
    args: [200],  // 200 bps = 2% tolerance (appropriate for thin BOB/PEN cross-rate)
    leaderOnly: false,
  });

  const receipt = await waitAccepted(client, hash, "deploy");
  const address = receipt.data?.contract_address || receipt.to_address;

  console.log(`\n📍 ForexOracle deployed at: ${address}`);

  // Persist address
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify({ address, deployed_at: new Date().toISOString() }, null, 2));
  console.log(`   Address saved → ${STATE_FILE}`);

  return address;
}

async function updateRate(address) {
  console.log(`\n🌐 Calling update_rate() on ${address}…`);
  console.log("   (Each of 5 validators will fetch from 3 sources independently)");
  const { client } = makeClient(PRIVATE_KEY);
  await fund(createAccount(PRIVATE_KEY).address);
  await client.initializeConsensusSmartContract();

  const hash = await client.writeContract({
    address,
    functionName: "update_rate",
    args: [],
    leaderOnly: false,
  });

  await waitAccepted(client, hash, "update_rate");
}

async function readRate(address) {
  console.log(`\n📊 Reading stored rate from ${address}…`);
  const { client } = makeClient(PRIVATE_KEY);

  const raw = await readWithRetry(client, address, "get_rate");
  const data = typeof raw === "string" ? JSON.parse(raw) : raw;

  console.log("\n┌─────────────────────────────────────────────┐");
  console.log(`│  BOB/PEN rate:     ${data.rate_str.padEnd(27)}│`);
  console.log(`│  rate_18 (Solidity):                        │`);
  console.log(`│  ${String(data.rate_18).padEnd(44)} │`);
  console.log(`│  Tolerance:        ${String(data.tolerance_bps + " bps").padEnd(25)}│`);
  console.log(`│  Last updated:     ${String(data.last_updated || "n/a").slice(0, 24).padEnd(25)}│`);
  console.log(`│  Update count:     ${String(data.update_count).padEnd(25)}│`);
  console.log("└─────────────────────────────────────────────┘");

  console.log(`\n📋 To use in Foundry deploy script:`);
  console.log(`   export FOREX_RATE_18=${data.rate_18}`);
  console.log(`   forge script script/Deploy.s.sol --rpc-url $RPC --broadcast --sig "run()" -vvv`);

  return data;
}

// --- main -------------------------------------------------------------------

const [,, command, address] = process.argv;

(async () => {
  try {
    switch (command) {
      case "deploy": {
        await deploy();
        break;
      }
      case "update": {
        if (!address) throw new Error("Usage: forex-oracle.mjs update <address>");
        await updateRate(address);
        break;
      }
      case "read": {
        if (!address) throw new Error("Usage: forex-oracle.mjs read <address>");
        await readRate(address);
        break;
      }
      case "run":
      default: {
        // Full flow: deploy → update → read
        let oracleAddr = address;
        if (!oracleAddr) {
          // Check if we have a saved address
          if (fs.existsSync(STATE_FILE)) {
            const saved = JSON.parse(fs.readFileSync(STATE_FILE));
            console.log(`ℹ️  Found saved oracle at ${saved.address} (deployed ${saved.deployed_at})`);
            console.log("   Use 'deploy' to force-redeploy, or 'update <addr>' to refresh rate.");
            oracleAddr = saved.address;
          } else {
            oracleAddr = await deploy();
          }
        }
        await updateRate(oracleAddr);
        await readRate(oracleAddr);
        break;
      }
    }
  } catch (err) {
    console.error("\n❌", err.message || err);
    process.exit(1);
  }
})();
