#!/usr/bin/env node
/**
 * deploy-v5-scenarios.mjs
 *
 * Deploys 3 fresh TradeFxSettlement v5 contracts (one per scenario A/B/C)
 * with bridgeReceiver wired, then runs each to CONTESTED state:
 *
 *   DRAFT → requestRateLock → receiveRate → RATE_LOCKED
 *         → fundSettlement   → FUNDED
 *         → contestShipment  → CONTESTED  ← relay picks up here
 *
 * Outputs artifacts/v5-scenarios.json with all addresses + tx hashes.
 */

import {
  createPublicClient, createWalletClient, http,
  parseUnits, parseAbi, encodeFunctionData
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { execSync } from "child_process";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT  = join(__dir, "..");
const RPC   = "https://sepolia.base.org";
const FORGE = `${process.env.HOME}/.foundry/bin/forge`;
const CAST  = `${process.env.HOME}/.foundry/bin/cast`;

// ── Known addresses ───────────────────────────────────────────────────────────
const MOCK_BOB        = "0xbd0ed9bc00b4dc90096bc3af7b3eb1080b4bc166";
const MOCK_PEN        = "0x08bc87f6511913caa4e127c5e4e91618a37a9719";
const ORACLE_RELAYER  = "0x7b9797c4c2DA625b120A27AD2c07bECB7A0E30fa";
const BRIDGE_RECEIVER = "0xc3e6aE892A704c875bF74Df46eD873308db15d82"; // InternetCourt BridgeReceiver Base Sepolia
const COURT_FACTORY   = "0xd533cB0B52E85b3F506b6f0c28b8f6bc4E449Dda"; // InternetCourtFactory v2 Base Sepolia
const EXPORTER        = "0xe9630ba0e3cc2d3BFC58fbE1Bbde478f06E4CE87";
const IMPORTER        = "0x942C20d078f7417aD67E96714310DA8068850B77";

// ── Keys ─────────────────────────────────────────────────────────────────────
function loadKey(path) {
  const k = readFileSync(path, "utf8").trim();
  return k.startsWith("0x") ? k : "0x" + k;
}

const EXPORTER_KEY = loadKey(`${process.env.HOME}/.internetcourt/.exporter_key`);
const IMPORTER_KEY = loadKey(`${process.env.HOME}/.internetcourt/.importer_key`);
const RELAYER_KEY  = loadKey(`${ROOT}/base-sepolia/.wallets/relayer.key`);

const exporterAcct = privateKeyToAccount(EXPORTER_KEY);
const importerAcct = privateKeyToAccount(IMPORTER_KEY);
const relayerAcct  = privateKeyToAccount(RELAYER_KEY);

const transport = http(RPC);
const pub = createPublicClient({ chain: baseSepolia, transport });
const exporterW = createWalletClient({ chain: baseSepolia, transport, account: exporterAcct });
const importerW = createWalletClient({ chain: baseSepolia, transport, account: importerAcct });
const relayerW  = createWalletClient({ chain: baseSepolia, transport, account: relayerAcct });

// ── Scenario definitions ──────────────────────────────────────────────────────
const SHEET_CIDS = JSON.parse(readFileSync(`${ROOT}/artifacts/new_court_sheet_cids_v2.json`, "utf8"));

const SCENARIOS = [
  {
    label:      "A_TIMELY",
    caseId:     "qc-coop-2026-0003",
    invoiceRef: "QC-COOP-2026-0003",
    sheetACid:  SHEET_CIDS["qc-coop-2026-0003"].a,
    sheetBCid:  SHEET_CIDS["qc-coop-2026-0003"].b,
  },
  {
    label:      "B_LATE",
    caseId:     "qc-coop-2026-0004",
    invoiceRef: "QC-COOP-2026-0004",
    sheetACid:  SHEET_CIDS["qc-coop-2026-0004"].a,
    sheetBCid:  SHEET_CIDS["qc-coop-2026-0004"].b,
  },
  {
    label:      "C_UNDETERMINED",
    caseId:     "qc-coop-2026-0005",
    invoiceRef: "QC-COOP-2026-0005",
    sheetACid:  SHEET_CIDS["qc-coop-2026-0005"].a,
    sheetBCid:  SHEET_CIDS["qc-coop-2026-0005"].b,
  },
];

const INVOICE_BOB   = parseUnits("150000", 18);   // 150,000 BOB
const RATE_18       = parseUnits("0.493", 18);     // 0.493 PEN/BOB
const ESCROW_PEN    = parseUnits("73950", 18);     // 73,950 PEN
const DUE_DATE      = Math.floor(Date.now() / 1000) + 90 * 86400; // 90 days

const STATEMENT = "Shipment under Contract ISPA-2025-BOL-PER-0047 crossed Bolivian export customs at Desaguadero on or before 2026-04-05T23:59:59-04:00.";
const GUIDELINE = "shipment-deadline-v1";

// ── ABIs ──────────────────────────────────────────────────────────────────────
const ERC20_ABI = parseAbi([
  "function approve(address spender, uint256 amount) returns (bool)",
  "function balanceOf(address) view returns (uint256)",
  "function mint(address to, uint256 amount)",
]);

const TFX_ABI = parseAbi([
  "function requestRateLock()",
  "function receiveRate(uint256 rate, bytes32 benchmarkType, bytes32 benchmarkId, uint256 asOfTimestamp)",
  "function fundSettlement()",
  "function contestShipment(string courtSheetACid, string courtSheetBCid, string statement, string guidelineVersion)",
  "function status() view returns (uint8)",
  "function shipmentStatus() view returns (uint8)",
  "function fundedAmount() view returns (uint256)",
  "function contestDeadline() view returns (uint256)",
  "function bridgeReceiver() view returns (address)",
]);

// ── Helpers ───────────────────────────────────────────────────────────────────
async function waitTx(hash, label) {
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60_000 });
  if (receipt.status !== "success") throw new Error(`${label} reverted: ${hash}`);
  console.log(`    ✅ ${label}: ${hash}`);
  return receipt;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Deploy one TradeFxSettlement ──────────────────────────────────────────────
function deployContract(invoiceRef) {
  const env = {
    ...process.env,
    DEPLOYER_KEY:         EXPORTER_KEY,
    EXPORTER_ADDR:        EXPORTER,
    IMPORTER_ADDR:        IMPORTER,
    ORACLE_RELAYER_ADDR:  ORACLE_RELAYER,
    ADMIN_ADDR:           "0x0000000000000000000000000000000000000000",
    SETTLEMENT_TOKEN:     MOCK_PEN,
    INVOICE_BOB:          INVOICE_BOB.toString(),
    INVOICE_REF:          invoiceRef,
    DUE_DATE_UNIX:        DUE_DATE.toString(),
    SOURCE_CURRENCY:      "BOB",
    SETTLEMENT_CURRENCY:  "PEN",
    BRIDGE_RECEIVER:      BRIDGE_RECEIVER,
    COURT_FACTORY:        COURT_FACTORY,
  };

  mkdirSync(`${ROOT}/base-sepolia/artifacts`, { recursive: true });

  const out = execSync(
    `${FORGE} script script/DeployTradeFx.s.sol --rpc-url ${RPC} --broadcast --sig "run()" -vv`,
    { cwd: `${ROOT}/base-sepolia`, env, encoding: "utf8" }
  );

  const manifest = JSON.parse(readFileSync(`${ROOT}/base-sepolia/artifacts/trade-fx-base-deployment.json`, "utf8"));
  return manifest.contract;
}

// ── Run scenario lifecycle ────────────────────────────────────────────────────
async function runScenario(scen) {
  console.log(`\n${"═".repeat(60)}`);
  console.log(`  Scenario ${scen.label} — ${scen.caseId}`);
  console.log(`${"═".repeat(60)}`);

  const result = { label: scen.label, caseId: scen.caseId, txs: {} };

  // 1. Deploy
  console.log("\n  [1] Deploying TradeFxSettlement...");
  const contractAddr = deployContract(scen.invoiceRef);
  result.contract = contractAddr;
  console.log(`    ✅ Deployed: ${contractAddr}`);

  // Small delay for node indexing
  await sleep(5000);

  // 2. Rate lock
  console.log("\n  [2] requestRateLock (exporter)...");
  result.txs.requestRateLock = await exporterW.writeContract({
    address: contractAddr, abi: TFX_ABI, functionName: "requestRateLock"
  }).then(h => waitTx(h, "requestRateLock")).then(r => r.transactionHash);

  await sleep(3000);

  // 3. receiveRate (relayer)
  console.log("\n  [3] receiveRate (relayer)...");
  result.txs.receiveRate = await relayerW.writeContract({
    address: contractAddr, abi: TFX_ABI, functionName: "receiveRate",
    args: [
      RATE_18,
      "0x4243525042434243524f5353000000000000000000000000000000000000000", // BCRP_BCB_CROSS
      "0x514332303236303030310000000000000000000000000000000000000000000", // QC2026001
      BigInt(Math.floor(Date.now() / 1000))
    ]
  }).then(h => waitTx(h, "receiveRate")).then(r => r.transactionHash);

  await sleep(3000);

  // 4. Approve MockPEN + fund
  console.log("\n  [4] Approve MockPEN + fundSettlement (importer)...");
  result.txs.approve = await importerW.writeContract({
    address: MOCK_PEN, abi: ERC20_ABI, functionName: "approve",
    args: [contractAddr, ESCROW_PEN]
  }).then(h => waitTx(h, "approve MockPEN")).then(r => r.transactionHash);

  await sleep(3000);

  result.txs.fundSettlement = await importerW.writeContract({
    address: contractAddr, abi: TFX_ABI, functionName: "fundSettlement"
  }).then(h => waitTx(h, "fundSettlement")).then(r => r.transactionHash);

  await sleep(3000);

  // 5. contestShipment (importer)
  console.log("\n  [5] contestShipment (importer)...");
  result.txs.contestShipment = await importerW.writeContract({
    address: contractAddr, abi: TFX_ABI, functionName: "contestShipment",
    args: [scen.sheetACid, scen.sheetBCid, STATEMENT, GUIDELINE]
  }).then(h => waitTx(h, "contestShipment")).then(r => r.transactionHash);

  // Verify CONTESTED
  const sStatus = await pub.readContract({ address: contractAddr, abi: TFX_ABI, functionName: "shipmentStatus" });
  console.log(`\n    shipmentStatus: ${sStatus} (expected 2=CONTESTED) ${sStatus === 2 ? "✅" : "❌"}`);
  result.shipmentStatus = sStatus;

  return result;
}

// ── Main ──────────────────────────────────────────────────────────────────────
(async () => {
  console.log("🚀 Deploying v5 scenario contracts...\n");

  // Verify token balances first
  const bobBal = await pub.readContract({ address: MOCK_BOB, abi: ERC20_ABI, functionName: "balanceOf", args: [EXPORTER] });
  const penBal = await pub.readContract({ address: MOCK_PEN, abi: ERC20_ABI, functionName: "balanceOf", args: [IMPORTER] });
  console.log(`MockBOB (exporter): ${Number(bobBal) / 1e18} BOB`);
  console.log(`MockPEN (importer): ${Number(penBal) / 1e18} PEN`);

  if (penBal < ESCROW_PEN * 3n) {
    console.log("⚠️  Minting more MockPEN for importer...");
    const h = await importerW.writeContract({
      address: MOCK_PEN, abi: ERC20_ABI, functionName: "mint",
      args: [IMPORTER, parseUnits("300000", 18)]
    });
    await waitTx(h, "mint MockPEN");
  }

  const results = [];
  for (const scen of SCENARIOS) {
    const r = await runScenario(scen);
    results.push(r);
    await sleep(5000);
  }

  mkdirSync(`${ROOT}/artifacts`, { recursive: true });
  writeFileSync(`${ROOT}/artifacts/v5-scenarios.json`, JSON.stringify(results, null, 2));

  console.log("\n\n" + "═".repeat(60));
  console.log("  ✅ ALL SCENARIOS DEPLOYED AND CONTESTED");
  console.log("═".repeat(60));
  results.forEach(r => {
    console.log(`\n  ${r.label}: ${r.contract}`);
    console.log(`    contest tx: ${r.txs.contestShipment}`);
  });
  console.log(`\n  Saved: artifacts/v5-scenarios.json`);
})().catch(e => { console.error("❌", e); process.exit(1); });
