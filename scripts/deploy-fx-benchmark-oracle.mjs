#!/usr/bin/env node
/**
 * deploy-fx-benchmark-oracle.mjs
 *
 * Deploy ONLY FxBenchmarkOracle.py to GenLayer Studionet.
 * Prints a machine-readable JSON summary to stdout.
 *
 * Required env:
 *   GL_PRIVATE_KEY
 */

import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const CONTRACT_PATH = path.join(ROOT, "contracts", "FxBenchmarkOracle.py");
const RPC = "https://studio.genlayer.com/api";
const EXPLORER = "https://explorer-studio.genlayer.com/transactions";

const GL_PK = process.env.GL_PRIVATE_KEY;
if (!GL_PK) {
  console.error("Missing GL_PRIVATE_KEY");
  process.exit(1);
}

const account = createAccount(GL_PK);
const client  = createClient({ chain: studionet, account });

async function fund(address) {
  try {
    await fetch(RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", method: "sim_fundAccount", params: [address, 10_000_000], id: 1 }),
    });
  } catch (_) {}
}

(async () => {
  await fund(account.address);
  await client.initializeConsensusSmartContract();

  const code = fs.readFileSync(CONTRACT_PATH, "utf8");
  const hash = await client.deployContract({ code, args: [200], leaderOnly: false });

  const receipt = await client.waitForTransactionReceipt({
    hash,
    status: TransactionStatus.ACCEPTED,
    retries: 180,
    interval: 5000,
  });

  const address = receipt.data?.contract_address || receipt.to_address;
  const ok = ["SUCCESS", "MAJORITY_AGREE", "AGREE"].includes(receipt.result_name) || receipt.result === 0;

  const out = {
    contract: "FxBenchmarkOracle",
    address,
    deployTx: hash,
    explorer: `${EXPLORER}/${hash}`,
    from: account.address,
    resultName: receipt.result_name,
    ok,
    deployedAt: new Date().toISOString(),
  };

  console.log(JSON.stringify(out, null, 2));
  if (!ok) process.exit(2);
})();
