/**
 * Bolivia → Peru Trade Finance System — GenLayer Studionet (Bradbury) Demo
 *
 * Requirements implemented:
 * - Deploy StableCoin twice (sBOB and sPEN)
 * - Deploy TradeFinanceDeal (exporter MUST be deployer)
 * - Run full flow with lots of logging + retries
 *
 * Run:
 *   npm install
 *   npm run deploy
 */

import { createClient, createAccount, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import fs from "fs";

const RPC = "https://studio.genlayer.com/api";

// ─────────────────────────────────────────────────────────────────────────────
// Logging helpers
// ─────────────────────────────────────────────────────────────────────────────

function ts() {
  return new Date().toISOString().replace("T", " ").split(".")[0];
}

function log(step, msg, obj) {
  const prefix = `[${ts()}] [${step}]`;
  if (obj !== undefined) console.log(prefix, msg, JSON.stringify(obj, null, 2));
  else console.log(prefix, msg);
}

function banner(title) {
  console.log("\n" + "═".repeat(78));
  console.log(title);
  console.log("═".repeat(78));
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function rpcCall(method, params) {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 }),
  });
  const data = await res.json();
  if (data.error) throw new Error(`${method} failed: ${data.error.message}`);
  return data.result;
}

async function fundAccount(address, amount = 10_000_000) {
  await rpcCall("sim_fundAccount", [address, amount]);
}

const SUCCESS_RESULTS = ["SUCCESS", "MAJORITY_AGREE", "AGREE", "AGREE_AND_CLOSE"];
function receiptOk(receipt) {
  return receipt.result === 0 || (receipt.result_name && SUCCESS_RESULTS.includes(receipt.result_name));
}

async function waitTx(client, hash, label, { retries = 180, interval = 5000 } = {}) {
  log("TX", `⏳ waiting for ${label} (tx=${hash}) — each write can take ~100s...`);
  const receipt = await client.waitForTransactionReceipt({
    hash,
    status: TransactionStatus.ACCEPTED,
    retries,
    interval,
  });
  if (!receiptOk(receipt)) {
    log("TX", `❌ ${label} failed`, receipt);
    throw new Error(`${label} failed (result=${receipt.result_name || receipt.result})`);
  }
  log("TX", `✅ ${label} ACCEPTED`, {
    tx: hash,
    result: receipt.result_name || receipt.result,
    contract_address: receipt.data?.contract_address,
  });
  return receipt;
}

async function readWithRetry(client, address, functionName, args = [], attempts = 6) {
  for (let i = 1; i <= attempts; i++) {
    try {
      return await client.readContract({ address, functionName, args });
    } catch (e) {
      if (i === attempts) throw e;
      log("READ", `read ${functionName} failed (attempt ${i}/${attempts}): ${e.message} — retrying...`);
      await sleep(i * 2500);
    }
  }
}

async function deploy(client, codePath, args, label) {
  const code = fs.readFileSync(codePath, "utf8");
  log("DEPLOY", `🚀 deploying ${label}...`, { args });
  const hash = await client.deployContract({ code, args, leaderOnly: false });
  log("DEPLOY", `${label} deploy tx`, { tx: hash });
  const receipt = await waitTx(client, hash, `deploy ${label}`);
  const addr = receipt.data?.contract_address || receipt.to_address;
  if (!addr) throw new Error(`missing contract address for ${label}`);
  log("DEPLOY", `${label} address`, { address: addr });
  await sleep(4000); // propagation delay
  return addr;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

async function main() {
  banner("🇧🇴→🇵🇪 Trade Finance on GenLayer (Studionet/Bradbury) — Bolivia Lithium Export");

  // a) Create two accounts
  banner("STEP A: Create accounts");
  const exporter = createAccount(generatePrivateKey());
  const importer = createAccount(generatePrivateKey());

  log("ACCT", "exporter (Bolivian lithium exporter)", { address: exporter.address });
  log("ACCT", "importer (Peruvian buyer)", { address: importer.address });

  const exporterClient = createClient({ chain: studionet, account: exporter });
  const importerClient = createClient({ chain: studionet, account: importer });

  // b) Fund both accounts
  banner("STEP B: Fund accounts via sim_fundAccount");
  await fundAccount(exporter.address, 10_000_000);
  await fundAccount(importer.address, 10_000_000);
  log("FUND", "funded both accounts", { exporter: exporter.address, importer: importer.address });

  // Consensus init
  banner("STEP B2: Initialize consensus smart contract (required before first tx)");
  await exporterClient.initializeConsensusSmartContract();
  await importerClient.initializeConsensusSmartContract();
  log("INIT", "consensus initialized for both clients");

  // c) Deploy three contracts
  banner("STEP C: Deploy contracts (sBOB, sPEN, TradeFinanceDeal)");
  const sBOB = await deploy(exporterClient, "contracts/StableCoin.py", ["Synthetic Boliviano", "sBOB", 18], "sBOB");
  const sPEN = await deploy(exporterClient, "contracts/StableCoin.py", ["Synthetic Sol", "sPEN", 18], "sPEN");

  const deadline = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  const deal = await deploy(
    exporterClient,
    "contracts/TradeFinanceDeal.py",
    [
      importer.address,
      "Lithium carbonate, 99.5% purity, 500MT, INCOTERMS CIF Callao port",
      "BOB",
      "PEN",
      "500000",
      "0.40",
      100,
      deadline,
    ],
    "TradeFinanceDeal"
  );

  log("ADDR", "contracts", { sBOB, sPEN, deal });

  // d) Execute full deal flow
  banner("STEP D1: Mint 500,000 sBOB to exporter");
  const mintBOB = "500000000000000000000000"; // 500k * 1e18
  let tx = await exporterClient.writeContract({ address: sBOB, functionName: "mint", args: [exporter.address, mintBOB], leaderOnly: false });
  await waitTx(exporterClient, tx, "mint sBOB");

  banner("STEP D2: Mint 100,000 sPEN to importer");
  const mintPEN = "100000000000000000000000"; // 100k * 1e18
  tx = await exporterClient.writeContract({ address: sPEN, functionName: "mint", args: [importer.address, mintPEN], leaderOnly: false });
  await waitTx(exporterClient, tx, "mint sPEN");

  // balances
  const expBOB0 = await readWithRetry(exporterClient, sBOB, "balance_of", [exporter.address]);
  const impPEN0 = await readWithRetry(importerClient, sPEN, "balance_of", [importer.address]);
  log("BAL", "post-mint balances (raw 18 decimals)", { exporter_sBOB: expBOB0, importer_sPEN: impPEN0 });

  banner("STEP D3: Deal already created at deploy time (500,000 BOB invoice, est 0.40 PEN/BOB)");
  const status0 = await readWithRetry(exporterClient, deal, "get_deal_status", []);
  log("DEAL", "initial status", typeof status0 === "string" ? JSON.parse(status0) : status0);

  banner("STEP D4: Importer funds escrow with sPEN (and transfers sPEN to deal address)");
  // Spec mentions ~200k PEN escrow but mints 100k PEN; we escrow the minted amount.
  const escrow = mintPEN;
  log("ESCROW", "escrowing (raw 18 decimals)", { escrow });

  // Transfer sPEN to deal address as the on-chain escrow representation
  const impPENBefore = await readWithRetry(importerClient, sPEN, "balance_of", [importer.address]);
  const dealPENBefore = await readWithRetry(exporterClient, sPEN, "balance_of", [deal]);
  log("ESCROW", "before transfer", { importer_sPEN: impPENBefore, deal_sPEN: dealPENBefore });

  tx = await importerClient.writeContract({ address: sPEN, functionName: "transfer", args: [deal, escrow], leaderOnly: false });
  await waitTx(importerClient, tx, "transfer sPEN to deal (escrow)");

  const impPENAfter = await readWithRetry(importerClient, sPEN, "balance_of", [importer.address]);
  const dealPENAfter = await readWithRetry(exporterClient, sPEN, "balance_of", [deal]);
  log("ESCROW", "after transfer", { importer_sPEN: impPENAfter, deal_sPEN: dealPENAfter });

  // Record escrow amount in the deal contract
  tx = await importerClient.writeContract({ address: deal, functionName: "fund_escrow", args: [escrow], leaderOnly: false });
  await waitTx(importerClient, tx, "fund_escrow" );

  banner("STEP D5: Exporter submits shipment proof");
  const shipmentProof = JSON.stringify({
    bill_of_lading: "COSCO-BOL-2026-001",
    container_ids: ["COSCO-U-123456-7", "COSCO-U-789012-3"],
    tracking: "https://track.example.test/COSCO-BOL-2026-001",
    goods: "Lithium carbonate (Li2CO3), 99.5% purity",
    quantity: "500 metric tons",
  });
  tx = await exporterClient.writeContract({ address: deal, functionName: "submit_shipment", args: [shipmentProof], leaderOnly: false });
  await waitTx(exporterClient, tx, "submit_shipment" );

  banner("STEP D6: Importer confirms delivery");
  const deliveryConf = JSON.stringify({
    confirmed: true,
    inspector: "SGS Peru S.A.",
    note: "Goods received at Callao port; purity confirmed.",
  });
  tx = await importerClient.writeContract({ address: deal, functionName: "confirm_delivery", args: [deliveryConf], leaderOnly: false });
  await waitTx(importerClient, tx, "confirm_delivery" );

  banner("STEP D7: Trigger AI settlement (validators fetch live BOB/PEN rate)");
  tx = await exporterClient.writeContract({ address: deal, functionName: "settle", args: [], leaderOnly: false });
  const settleReceipt = await waitTx(exporterClient, tx, "settle (AI)", { retries: 240, interval: 5000 });
  log("AI", "settlement receipt (consensus)", {
    tx,
    result_name: settleReceipt.result_name,
    result: settleReceipt.result,
  });

  banner("STEP D8: Print final settlement details");
  const full = await readWithRetry(exporterClient, deal, "get_full_details", []);
  const details = typeof full === "string" ? JSON.parse(full) : full;

  const forex = await readWithRetry(exporterClient, deal, "get_forex_details", []);
  const forexDetails = typeof forex === "string" ? JSON.parse(forex) : forex;

  // Log rate vs estimate
  const est = parseFloat(details.estimated_rate || "0.40");
  const actual = parseFloat(forexDetails.settlement_rate || "0");
  const deviationBps = est > 0 ? (Math.abs(actual - est) / est) * 10000 : null;

  log("FINAL", "settlement summary", {
    status: details.status,
    estimated_rate: details.estimated_rate,
    settlement_rate: forexDetails.settlement_rate,
    deviation_bps: deviationBps,
    within_tolerance_bps: details.rate_tolerance_bps,
    final_amount_pen: forexDetails.final_amount,
    rate_source: forexDetails.rate_source,
  });

  // Before/after balances snapshot
  const expBOB1 = await readWithRetry(exporterClient, sBOB, "balance_of", [exporter.address]);
  const impPEN1 = await readWithRetry(importerClient, sPEN, "balance_of", [importer.address]);
  const dealPEN1 = await readWithRetry(exporterClient, sPEN, "balance_of", [deal]);

  log("FINAL", "balances (raw 18 decimals)", {
    exporter_sBOB: expBOB1,
    importer_sPEN: impPEN1,
    deal_sPEN_escrow: dealPEN1,
  });

  // Print everything
  console.log("\nFULL DEAL DETAILS:\n" + JSON.stringify(details, null, 2));

  banner("DONE");
  log("DONE", "completed deployment + execution", { sBOB, sPEN, deal, exporter: exporter.address, importer: importer.address });
}

main().catch((e) => {
  console.error("\n[ERROR]", e.message);
  console.error(e.stack);
  process.exit(1);
});
