/**
 * Deploy InternetCourt + run multimodal dispute with real evidence images.
 *
 * This script:
 * 1. Creates exporter + importer accounts
 * 2. Deploys InternetCourt contract
 * 3. Party B accepts
 * 4. Initiates dispute
 * 5. Both parties submit evidence (text + document image URLs)
 * 6. Triggers AI jury resolve() — validators fetch images and analyze visually
 *
 * Run: node scripts/deploy-dispute.mjs
 */

import { createClient, createAccount, generatePrivateKey } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import fs from "fs";

const RPC = "https://studio.genlayer.com/api";

const EVIDENCE_BASE = "https://raw.githubusercontent.com/acastellana/apps/main/trade-finance/evidence-compact";

// ── Helpers ──

function ts() { return new Date().toISOString().replace("T", " ").split(".")[0]; }
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
async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
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

async function waitTx(client, hash, label, { retries = 300, interval = 5000 } = {}) {
  log("TX", `⏳ waiting for ${label} (tx=${hash})...`);
  const receipt = await client.waitForTransactionReceipt({
    hash, status: TransactionStatus.ACCEPTED, retries, interval,
  });
  if (!receiptOk(receipt)) {
    log("TX", `❌ ${label} failed`, receipt);
    throw new Error(`${label} failed (result=${receipt.result_name || receipt.result})`);
  }
  log("TX", `✅ ${label} ACCEPTED`, {
    tx: hash, result: receipt.result_name || receipt.result,
    contract_address: receipt.data?.contract_address,
  });
  return receipt;
}

async function deploy(client, codePath, args, label) {
  const code = fs.readFileSync(codePath, "utf8");
  log("DEPLOY", `🚀 deploying ${label}...`, { args });
  const hash = await client.deployContract({ code, args, leaderOnly: false });
  const receipt = await waitTx(client, hash, `deploy ${label}`);
  const addr = receipt.data?.contract_address || receipt.to_address;
  if (!addr) throw new Error(`missing contract address for ${label}`);
  log("DEPLOY", `${label} address: ${addr}`);
  await sleep(3000);
  return addr;
}

async function writeContract(client, address, functionName, args, label) {
  const hash = await client.writeContract({ address, functionName, args, leaderOnly: false });
  return await waitTx(client, hash, label);
}

async function readContract(client, address, functionName, args = []) {
  for (let i = 1; i <= 5; i++) {
    try {
      return await client.readContract({ address, functionName, args });
    } catch (e) {
      if (i === 5) throw e;
      await sleep(i * 2000);
    }
  }
}

// ── Main ──

async function main() {
  banner("🧑‍⚖️ InternetCourt Dispute — Multimodal Evidence Analysis");

  // 1) Create accounts
  banner("STEP 1: Create accounts");
  const exporter = createAccount(generatePrivateKey());
  const importer = createAccount(generatePrivateKey());
  log("ACCT", "exporter (Minera Andina SRL — Party A)", { address: exporter.address });
  log("ACCT", "importer (Electroquímica del Perú — Party B)", { address: importer.address });

  const exporterClient = createClient({ chain: studionet, account: exporter });
  const importerClient = createClient({ chain: studionet, account: importer });

  await fundAccount(exporter.address, 10_000_000);
  await fundAccount(importer.address, 10_000_000);
  await exporterClient.initializeConsensusSmartContract();
  await importerClient.initializeConsensusSmartContract();
  log("FUND", "both accounts funded + consensus initialized");

  // 2) Deploy InternetCourt
  banner("STEP 2: Deploy InternetCourt contract");

  const statement = `Minera Andina SRL delivered 50 metric tons of battery-grade lithium carbonate (Li2CO3) meeting ISO 6206:2023 purity standards (minimum 99.0%) to Callao port, Peru, in conformity with Purchase Order EP-PO-2026-0178.`;

  const guidelines = `Evaluate the following:
1. Was the material at or above 99.0% Li2CO3 purity when loaded at Antofagasta?
2. Were the shipping containers in adequate condition to preserve product quality?
3. If degradation occurred in transit, was it the seller's responsibility (CIF terms, Incoterms 2020)?
4. Consider the chain of custody: pre-shipment inspection vs. arrival inspection.
5. Weigh accredited lab results (ISO/IEC 17025) more heavily than unaccredited claims.
6. Under CIF terms, risk passes to buyer when goods cross the ship's rail at the port of loading.`;

  const evidenceDefs = `Party A (Exporter) may submit:
- Pre-shipment Certificate of Analysis from accredited lab
- Pre-shipment inspection report with container seal records
- Bill of Lading with shipping details

Party B (Importer) may submit:
- Independent arrival analysis from accredited lab
- Arrival inspection report with container condition photos
- Formal rejection notice with claimed damages
- Purchase contract excerpt with quality specifications`;

  const courtAddr = await deploy(
    exporterClient,
    "contracts/InternetCourt.py",
    [importer.address, statement, guidelines, evidenceDefs],
    "InternetCourt"
  );

  // 3) Party B accepts
  banner("STEP 3: Party B accepts the contract");
  await writeContract(importerClient, courtAddr, "accept_contract", [], "accept_contract");

  // 4) Initiate dispute
  banner("STEP 4: Initiate dispute");
  await writeContract(exporterClient, courtAddr, "initiate_dispute", [], "initiate_dispute");

  // 5) Submit evidence
  banner("STEP 5: Exporter submits evidence (with document images)");

  const exporterEvidence = JSON.stringify({
    text: `Minera Andina SRL asserts full compliance with PO EP-PO-2026-0178:

1. PURITY: SGS Chile Certificate of Analysis (CL-ANT-2026-04871) confirms Li2CO3 purity of 99.12%, tested per ISO 6206:2023 by ICP-OES at SGS's ISO/IEC 17025 accredited lab (LE-1247). All 10 parameters PASS.

2. PRE-SHIPMENT: SGS pre-shipment inspection (CL-ANT-PSI-2026-01203) on 2026-01-22 confirms:
   - All 2,000 bags (50 MT) in perfect condition
   - All 4 containers clean, dry, structurally sound
   - SGS bolt seals applied (SGS-CL-880214 through 880217)
   - Zero defects found, desiccant strips installed

3. SHIPPING: COSCO Bill of Lading (COSU-BOL-2026-001847) confirms goods loaded in apparent good order on 2026-01-24.

4. RISK: Under CIF Incoterms 2020, risk transferred to buyer at port of loading (Antofagasta). Any transit damage is buyer's marine insurance claim, not seller's liability.

5. If containers 3 and 4 had seal/gasket issues, this occurred AFTER the goods left the seller's control with verified SGS seals intact.`,
    documents: [
      { url: `${EVIDENCE_BASE}/01_SGS_Certificate_of_Analysis.jpg`, label: "SGS Certificate of Analysis — Li2CO3 purity 99.12%" },
    ],
  });

  await writeContract(exporterClient, courtAddr, "submit_evidence", [exporterEvidence], "submit_evidence (exporter)");

  banner("STEP 6: Importer submits evidence (with document images)");

  const importerEvidence = JSON.stringify({
    text: `Electroquímica del Perú S.A. rejects the shipment for non-conformity:

1. PURITY FAILURE: Bureau Veritas Lima (BV-LIM-2026-AN-00412) independent analysis shows:
   - Containers 1 & 2: 98.87% purity (below 99.0% spec)
   - Containers 3 & 4: 97.54% purity (severe failure)
   - Weighted average: 98.54% — 0.46 points below ISO 6206 minimum
   - BV Lima is INACAL-DA accredited (LP-042-2024)

2. CONTAINER DAMAGE: Arrival inspection (EP-QC-INS-2026-0089) found:
   - Container COSCU-123458-3: corroded seal (SGS-CL-880216), visible fracture
   - Container COSCU-123459-1: degraded door gasket, moisture on walls, ~15 damp bags
   - 62 photographs documenting the damage

3. EVEN "GOOD" CONTAINERS FAIL: Containers 1 and 2, with intact seals, still test at 98.87% — below the 99.0% specification. This suggests the original material may have been closer to the boundary than the SGS certificate indicates.

4. Note that SGS used ICP-OES while BV used ICP-MS (higher sensitivity). The SGS result of 99.12% may have rounded favorably.

5. COMMERCIAL IMPACT: Material is unsuitable for NMC 811 cathode production. We demand full replacement or 35% price reduction plus USD 34,000 in damages.`,
    documents: [
      { url: `${EVIDENCE_BASE}/04_BureauVeritas_Lab_Analysis.jpg`, label: "Bureau Veritas Independent Lab Analysis — purity 98.54%" },
    ],
  });

  await writeContract(importerClient, courtAddr, "submit_evidence", [importerEvidence], "submit_evidence (importer)");

  // 6) Trigger AI jury resolution
  banner("STEP 7: Trigger AI Jury Resolution (multimodal — analyzing 7 document images)");
  log("JURY", "⚖️ AI validators will fetch and visually analyze all 7 evidence documents...");
  log("JURY", "This may take 2-5 minutes as validators process images + reach consensus.");

  await writeContract(exporterClient, courtAddr, "resolve", [], "resolve (AI jury)");

  // 7) Read verdict
  banner("STEP 8: Read verdict");
  const verdict = await readContract(exporterClient, courtAddr, "get_verdict");
  const parsed = typeof verdict === "string" ? JSON.parse(verdict) : verdict;
  log("VERDICT", "⚖️ AI JURY VERDICT", parsed);

  const details = await readContract(exporterClient, courtAddr, "get_contract_details");
  const detailsParsed = typeof details === "string" ? JSON.parse(details) : details;
  console.log("\nFULL CONTRACT DETAILS:\n" + JSON.stringify(detailsParsed, null, 2));

  banner("DONE");
  log("DONE", "InternetCourt dispute resolved", {
    court: courtAddr,
    exporter: exporter.address,
    importer: importer.address,
    verdict: parsed.verdict,
  });
}

main().catch(e => {
  console.error("\n[ERROR]", e.message);
  console.error(e.stack);
  process.exit(1);
});
