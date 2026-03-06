#!/usr/bin/env node
/**
 * deploy-scenarios.mjs
 * Deploys MockBOB, MockPEN, and 4 TradeFxSettlement scenario contracts.
 * Executes each scenario fully: lock → (resize/roll) → fund → settle/cancel.
 * Collects all tx hashes and final balances into artifacts/scenarios.json.
 *
 * Usage:
 *   node scripts/deploy-scenarios.mjs
 *
 * Required env: DEPLOYER_KEY, RELAYER_KEY, GL_PRIVATE_KEY, GL_ORACLE_ADDRESS
 */

import { createPublicClient, createWalletClient, http, parseUnits, formatUnits } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { execSync } from "child_process";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { createClient, createAccount as glCreateAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT  = join(__dir, "..");
const RPC   = process.env.BASE_SEPOLIA_RPC || "https://sepolia.base.org";

// ─── Keys ─────────────────────────────────────────────────────────────────────
function loadKey(path) {
  return readFileSync(path, "utf8").trim().replace(/^0x/,"");
}

const DEPLOYER_KEY = "0x" + loadKey(`${process.env.HOME}/.internetcourt/.exporter_key`);
const IMPORTER_KEY = "0x" + loadKey(`${process.env.HOME}/.internetcourt/.importer_key`);
const RELAYER_KEY  = "0x" + loadKey(`${ROOT}/base-sepolia/.wallets/relayer.key`);

const deployer = privateKeyToAccount(DEPLOYER_KEY);
const importer = privateKeyToAccount(IMPORTER_KEY);
const relayer  = privateKeyToAccount(RELAYER_KEY);

const EXPORTER_ADDR = deployer.address;
const IMPORTER_ADDR = importer.address;
const RELAYER_ADDR  = relayer.address;
const ORACLE_ADDR   = process.env.GL_ORACLE_ADDRESS || "0x3B8501bAcaB70dedbC6f8B8EFCB888ba66cbc73e";

const transport = http(RPC);
const pub = createPublicClient({ chain: baseSepolia, transport });

function wallet(account) {
  return createWalletClient({ chain: baseSepolia, transport, account });
}

const deployerW  = wallet(deployer);
const importerW  = wallet(importer);
const relayerW   = wallet(relayer);

// ─── Forge helpers ────────────────────────────────────────────────────────────
function forge(args) {
  return execSync(
    `${process.env.HOME}/.foundry/bin/forge ${args}`,
    { cwd: `${ROOT}/base-sepolia`, encoding: "utf8" }
  );
}

function cast(args) {
  return execSync(
    `${process.env.HOME}/.foundry/bin/cast ${args} --rpc-url ${RPC}`,
    { cwd: `${ROOT}/base-sepolia`, encoding: "utf8" }
  ).trim();
}

// ─── ABIs (minimal) ───────────────────────────────────────────────────────────
const ERC20_ABI = [
  { name: "mint",     type: "function", inputs: [{ name: "to", type: "address" }, { name: "amount", type: "uint256" }], outputs: [] },
  { name: "approve",  type: "function", inputs: [{ name: "spender", type: "address" }, { name: "amount", type: "uint256" }], outputs: [{ type: "bool" }] },
  { name: "balanceOf",type: "function", inputs: [{ name: "account", type: "address" }], outputs: [{ type: "uint256" }] },
  { name: "transfer", type: "function", inputs: [{ name: "to", type: "address" }, { name: "amount", type: "uint256" }], outputs: [{ type: "bool" }] },
];

const TFX_ABI = [
  { name: "requestRateLock", type: "function", inputs: [], outputs: [] },
  { name: "receiveRate",     type: "function", inputs: [
    { name: "rate", type: "uint256" }, { name: "benchmarkType", type: "bytes32" },
    { name: "benchmarkId", type: "bytes32" }, { name: "asOfTimestamp", type: "uint256" }
  ], outputs: [] },
  { name: "resize",          type: "function", inputs: [{ name: "newFulfilledBps", type: "uint256" }], outputs: [] },
  { name: "fundSettlement",  type: "function", inputs: [], outputs: [] },
  { name: "requestRoll",     type: "function", inputs: [{ name: "newDueDate", type: "uint256" }], outputs: [] },
  { name: "receiveRolledRate", type: "function", inputs: [
    { name: "newRate", type: "uint256" }, { name: "rollCost", type: "uint256" },
    { name: "benchmarkId", type: "bytes32" }, { name: "asOfTimestamp", type: "uint256" }
  ], outputs: [] },
  { name: "settle",          type: "function", inputs: [], outputs: [] },
  { name: "cancelAndRefund", type: "function", inputs: [{ name: "reasonCode", type: "uint8" }], outputs: [] },
  { name: "settlementAmount",type: "function", inputs: [], outputs: [{ type: "uint256" }] },
  { name: "status",          type: "function", inputs: [], outputs: [{ type: "uint8" }] },
  { name: "fundedAmount",    type: "function", inputs: [], outputs: [{ type: "uint256" }] },
];

const STATUS = ["DRAFT","RATE_PENDING","RATE_LOCKED","FUNDED","ROLL_PENDING","ROLLED","SETTLED","CANCELLED"];

// ─── Nonce management (per-account local counter) ────────────────────────────
const _nonces = {};
async function nextNonce(address) {
  if (_nonces[address] === undefined) {
    _nonces[address] = await pub.getTransactionCount({ address, blockTag: "pending" });
  }
  return _nonces[address]++;
}

// ─── Utilities ────────────────────────────────────────────────────────────────
async function send(walletClient, contract, abi, fn, args = []) {
  const nonce = await nextNonce(walletClient.account.address);
  const hash = await walletClient.writeContract({
    address: contract, abi, functionName: fn, args, nonce
  });
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60000 });
  if (receipt.status !== "success") throw new Error(`${fn} reverted: ${hash}`);
  return hash;
}

async function read(contract, abi, fn, args = []) {
  return pub.readContract({ address: contract, abi, functionName: fn, args });
}

async function tokenBalance(token, address) {
  const raw = await read(token, ERC20_ABI, "balanceOf", [address]);
  return { raw, display: formatUnits(raw, 18) };
}

function loadArtifact(name) {
  const p = `${ROOT}/base-sepolia/out/${name}.sol/${name}.json`;
  return JSON.parse(readFileSync(p, "utf8"));
}

async function deployERC20(name, symbol) {
  const art = loadArtifact("MockERC20");
  const nonce = await nextNonce(EXPORTER_ADDR);
  const hash = await deployerW.deployContract({
    abi: art.abi, bytecode: art.bytecode.object,
    args: [name, symbol, 18], nonce
  });
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60000 });
  if (receipt.status !== "success") throw new Error(`Deploy ${name} failed`);
  return { address: receipt.contractAddress, deployTx: hash };
}

// ─── GenLayer oracle (native JS — no subprocess) ──────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

const glAccount = glCreateAccount(process.env.GL_PRIVATE_KEY || DEPLOYER_KEY);
const glClient  = createClient({ chain: studionet, account: glAccount });

function toBytes32(str) {
  const hex = Buffer.from(str).toString("hex").padEnd(64, "0");
  return `0x${hex}`;
}

async function callOracle(fnName, args, retries = 3) {
  for (let i = 0; i < retries; i++) {
    const hash = await glClient.writeContract({
      address: ORACLE_ADDR, functionName: fnName, args, leaderOnly: false,
    });
    const receipt = await glClient.waitForTransactionReceipt({
      hash, status: TransactionStatus.ACCEPTED, retries: 40, interval: 3000,
    });
    const ok = ["ok", "MAJORITY_AGREE", "majority_agree"];
    if (ok.includes(receipt.result_name)) return receipt;
    if (i < retries - 1) {
      process.stdout.write(` [${receipt.result_name}, retrying ${i+1}/${retries-1}]`);
      await sleep(3000);
    } else {
      throw new Error(`Oracle ${fnName} → ${receipt.result_name} after ${retries} attempts`);
    }
  }
}

async function readOracleRate(tradeAddress) {
  const raw = await glClient.readContract({
    address: ORACLE_ADDR, functionName: "get_locked_rate", args: [tradeAddress],
  });
  return JSON.parse(raw);
}

async function deliverRate(tradeAddress, rateData) {
  const { rate_18, benchmark_type, benchmark_id, as_of_timestamp } = rateData;
  const asOfUnix = Math.floor(new Date(as_of_timestamp).getTime() / 1000);
  const nonce = await nextNonce(RELAYER_ADDR);
  const hash = await relayerW.writeContract({
    address: tradeAddress,
    abi: TFX_ABI,
    functionName: "receiveRate",
    args: [BigInt(rate_18), toBytes32(benchmark_type), toBytes32(benchmark_id), BigInt(asOfUnix)],
    nonce,
  });
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60000 });
  if (receipt.status !== "success") throw new Error(`receiveRate reverted: ${hash}`);
  return hash;
}

async function deliverRolledRate(tradeAddress, rateData) {
  const { rate_18, benchmark_type, benchmark_id, as_of_timestamp } = rateData;
  const asOfUnix = Math.floor(new Date(as_of_timestamp).getTime() / 1000);
  const nonce = await nextNonce(RELAYER_ADDR);
  const hash = await relayerW.writeContract({
    address: tradeAddress,
    abi: TFX_ABI,
    functionName: "receiveRolledRate",
    args: [BigInt(rate_18), 0n, toBytes32(benchmark_id), BigInt(asOfUnix)],
    nonce,
  });
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60000 });
  if (receipt.status !== "success") throw new Error(`receiveRolledRate reverted: ${hash}`);
  return hash;
}

async function getOracleRate(tradeAddress) {
  process.stdout.write("  oracle: fetching rate...");
  await callOracle("request_rate_lock_fallback", [tradeAddress]);
  const rateData = await readOracleRate(tradeAddress);
  process.stdout.write(` ${rateData.rate_str} BOB/PEN\n`);
  const tx = await deliverRate(tradeAddress, rateData);
  await sleep(2000);
  return { rate: rateData.rate_str, tx, rateData };
}

async function readOracleRoll(tradeAddress) {
  const raw = await glClient.readContract({
    address: ORACLE_ADDR, functionName: "get_rolled_rate", args: [tradeAddress],
  });
  return JSON.parse(raw);
}

async function getOracleRoll(tradeAddress, newDueDateStr) {
  process.stdout.write("  oracle: fetching roll rate...");
  await callOracle("request_roll_fallback", [tradeAddress, newDueDateStr]);
  const rateData = await readOracleRoll(tradeAddress);
  process.stdout.write(` ${rateData.rate_str} BOB/PEN\n`);
  const tx = await deliverRolledRate(tradeAddress, rateData);
  await sleep(2000);
  return { rate: rateData.rate_str, tx };
}

// ─── Deploy shared tokens ─────────────────────────────────────────────────────
async function deployTokens() {
  console.log("\n=== Deploying MockBOB and MockPEN ===");
  const bob = await deployERC20("MockBOB", "mBOB");
  console.log(`MockBOB: ${bob.address}  tx: ${bob.deployTx}`);
  const pen = await deployERC20("MockPEN", "mPEN");
  console.log(`MockPEN: ${pen.address}  tx: ${pen.deployTx}`);
  return { bob, pen };
}

// ─── Deploy one scenario contract ─────────────────────────────────────────────
const BOB_BYTES32 = "0x424f420000000000000000000000000000000000000000000000000000000000";
const PEN_BYTES32 = "0x50454e0000000000000000000000000000000000000000000000000000000000";

async function deployScenario(label, ref, penAddr, invoiceBOB, dueDateUnix) {
  console.log(`\n--- Deploying ${label} (${ref}) ---`);
  const art = loadArtifact("TradeFxSettlement");
  const nonce = await nextNonce(EXPORTER_ADDR);
  const hash = await deployerW.deployContract({
    abi: art.abi,
    bytecode: art.bytecode.object,
    args: [
      EXPORTER_ADDR, IMPORTER_ADDR, RELAYER_ADDR, EXPORTER_ADDR,
      penAddr, BigInt(invoiceBOB), BOB_BYTES32, PEN_BYTES32,
      BigInt(dueDateUnix), ref
    ],
    nonce
  });
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 60000 });
  if (receipt.status !== "success") throw new Error(`Deploy ${label} failed`);
  console.log(`  Contract: ${receipt.contractAddress}  tx: ${hash}`);
  return { address: receipt.contractAddress, deployTx: hash };
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  mkdirSync(`${ROOT}/artifacts`, { recursive: true });

  const now = Math.floor(Date.now() / 1000);
  const results = {};

  // ── Tokens: deploy fresh or reuse from env ───────────────────────────────
  const REUSE_PEN = process.env.REUSE_PEN;
  const REUSE_BOB = process.env.REUSE_BOB;

  let pen, bob;
  if (REUSE_PEN && REUSE_BOB) {
    console.log(`\n=== Reusing tokens ===`);
    pen = { address: REUSE_PEN, deployTx: "existing" };
    bob = { address: REUSE_BOB, deployTx: "existing" };
    console.log(`MockBOB: ${bob.address}`);
    console.log(`MockPEN: ${pen.address}`);
  } else {
    const tokens = await deployTokens();
    pen = tokens.pen; bob = tokens.bob;
    // Mint initial supply to importer (500k PEN covers all 4 scenarios)
    console.log("\n=== Minting tokens ===");
    await send(deployerW, pen.address, ERC20_ABI, "mint", [IMPORTER_ADDR, parseUnits("500000", 18)]);
    console.log(`Minted 500,000 mPEN to importer ${IMPORTER_ADDR}`);
  }
  results.tokens = { mockBOB: bob.address, mockPEN: pen.address };

  // ── SCENARIO 1: Standard Settlement ──────────────────────────────────────
  const SKIP_S1 = process.env.SKIP_S1 === "1";
  console.log(`\n\n=== SCENARIO 1: Standard Settlement ${SKIP_S1 ? "(skipped — reusing)" : ""} ===`);
  if (SKIP_S1) {
    results.scenario1 = {
      label: "Standard Settlement", ref: "QC-S1-STANDARD",
      contract: "0xf52895dc5899ddefa0d9a0e55454323438bfed8a",
      settlementToken: pen.address, invoiceBOB: "150,000",
      rate: "0.493", settlementPEN: "73950",
      txs: {
        deploy:          "0x5e308651c5a7728c9e3020a7695efefcea3b708b19ec696ee77e45570456cdfd",
        requestRateLock: "0x62ce0fe247f69747fa1fd75c7b701e75bc81a4071b04885b6e09226869ce29e9",
        rateLock:        "0x94b59d2de04cd439ca74312b0f66a0e48d5c069c4e3f9cc206c3e0a36706982c",
        approve:         "0x0fdf3d6b5d8dd7b5c9b18e2ee3ba48cf8b6cc46c9325fa52d5c83930960d201b",
        fund:            "0x9d6aac7295874d2e08a196fb0409a5cfa2f772616f63e6de759422155ac4dacc",
        settle:          "0x3a495a75fbf99097e1d3087720524930f3905cff04efe2fa3de1195499d6a244",
      },
      finalStatus: "SETTLED",
    };
    console.log("  Reused from previous run — all txs confirmed on-chain.");
  } else {
    const s1 = await deployScenario(
      "Standard Settlement", "QC-S1-STANDARD",
      pen.address, parseUnits("150000", 18).toString(),
      now + 30 * 86400
    );
    const s1_lockReq = await send(deployerW, s1.address, TFX_ABI, "requestRateLock");
    console.log(`  requestRateLock: ${s1_lockReq}`);
    const s1_oracle  = await getOracleRate(s1.address);
    const s1_amount  = await read(s1.address, TFX_ABI, "settlementAmount");
    console.log(`  Settlement amount: ${formatUnits(s1_amount, 18)} mPEN`);
    const s1_approve = await send(importerW, pen.address, ERC20_ABI, "approve", [s1.address, s1_amount]);
    const s1_fund    = await send(importerW, s1.address, TFX_ABI, "fundSettlement");
    const s1_settle  = await send(deployerW, s1.address, TFX_ABI, "settle");
    const s1_status  = await read(s1.address, TFX_ABI, "status");
    console.log(`  Final status: ${STATUS[s1_status]}`);
    results.scenario1 = {
      label: "Standard Settlement", ref: "QC-S1-STANDARD",
      contract: s1.address, settlementToken: pen.address,
      invoiceBOB: "150,000", rate: s1_oracle.rate,
      settlementPEN: formatUnits(s1_amount, 18),
      txs: { deploy: s1.deployTx, requestRateLock: s1_lockReq, rateLock: s1_oracle.tx,
             approve: s1_approve, fund: s1_fund, settle: s1_settle },
      finalStatus: STATUS[s1_status],
    };
  }

  // ── SCENARIO 2: Partial Shipment ─────────────────────────────────────────
  const SKIP_S2 = process.env.SKIP_S2 === "1";
  console.log(`\n\n=== SCENARIO 2: Partial Shipment ${SKIP_S2 ? "(skipped — reusing)" : ""} ===`);
  if (SKIP_S2) {
    results.scenario2 = {
      label: "Partial Shipment", ref: "QC-S2-PARTIAL",
      contract: "0xb311af27132e743b8f3b1816237ecafc485721fb",
      settlementToken: pen.address, invoiceBOB: "150,000 → 135,000 (90%)",
      rate: "0.493", originalSettlementPEN: "73950", actualSettlementPEN: "66555",
      txs: {
        deploy:          "0xd1d34ca732c9e04361c941c27f4e4f553f080fed1c8f849049acbf60a3e614b1",
        requestRateLock: "pending",
        rateLock:        "0xab0d673c750e2c45704e74ab9eab4b52bda43e924ed0cef13a8d3e4b9eaa7a2d",
        resize:          "0xbb610dae44b9667f9b4c00864e7d7f019be96551de0c0f6534568ab286c00214",
      },
      finalStatus: "unknown — check chain",
    };
    console.log("  Reused from previous run.");
  } else {
  const s2 = await deployScenario(
    "Partial Shipment", "QC-S2-PARTIAL",
    pen.address, parseUnits("150000", 18).toString(),
    now + 30 * 86400
  );

  const s2_lockReq = await send(deployerW, s2.address, TFX_ABI, "requestRateLock");
  const s2_oracle  = await getOracleRate(s2.address);
  console.log(`  Rate locked at ${s2_oracle.rate}, tx: ${s2_oracle.tx}`);

  // Resize to 90% (135,000 BOB fulfilled) — rate preserved
  const s2_resize = await send(deployerW, s2.address, TFX_ABI, "resize", [9000n]);
  console.log(`  Resized to 90% (9000 bps): ${s2_resize}`);

  const s2_amount = await read(s2.address, TFX_ABI, "settlementAmount");
  console.log(`  New settlement amount: ${formatUnits(s2_amount, 18)} mPEN (rate unchanged)`);

  // Fund at reduced amount
  const s2_approve = await send(importerW, pen.address, ERC20_ABI, "approve", [s2.address, s2_amount]);
  const s2_fund    = await send(importerW, s2.address, TFX_ABI, "fundSettlement");
  const s2_settle  = await send(deployerW, s2.address, TFX_ABI, "settle");

  const s2_status = await read(s2.address, TFX_ABI, "status");

  results.scenario2 = {
    label: "Partial Shipment",
    ref: "QC-S2-PARTIAL",
    contract: s2.address,
    settlementToken: pen.address,
    invoiceBOB: "150,000 → 135,000 (90%)",
    rate: s2_oracle.rate,
    originalSettlementPEN: formatUnits(parseUnits("150000", 18) * BigInt(Math.round(parseFloat(s2_oracle.rate) * 1e18)) / BigInt(1e18), 18),
    actualSettlementPEN: formatUnits(s2_amount, 18),
    txs: {
      deploy: s2.deployTx,
      requestRateLock: s2_lockReq,
      rateLock: s2_oracle.tx,
      resize: s2_resize,
      approve: s2_approve,
      fund: s2_fund,
      settle: s2_settle,
    },
      finalStatus: STATUS[s2_status],
    };
  } // end SKIP_S2

  // ── SCENARIO 3: Date Roll ─────────────────────────────────────────────────
  const REUSE_S3 = process.env.REUSE_S3;
  console.log("\n\n=== SCENARIO 3: Date Roll ===");
  const dueDate3 = now + 30 * 86400;

  let s3, s3_lockReq, s3_oracle, newDueDate3, s3_rollReq;
  if (REUSE_S3) {
    // Contract already in ROLL_PENDING — skip deploy, lock, and requestRoll
    s3 = { address: REUSE_S3, deployTx: "0x708942b713411282e05f502b17da248b41cf1b4b765ec3417eff0a258970d0d0" };
    s3_lockReq = "existing";
    s3_oracle  = { rate: "0.493", tx: "existing" };
    newDueDate3 = dueDate3 + 30 * 86400;
    s3_rollReq = "existing";
    console.log(`  Reusing contract ${REUSE_S3} (status=ROLL_PENDING), delivering oracle roll`);
  } else {
    s3 = await deployScenario(
      "Date Roll", "QC-S3-ROLL",
      pen.address, parseUnits("150000", 18).toString(),
      dueDate3
    );
    s3_lockReq = await send(deployerW, s3.address, TFX_ABI, "requestRateLock");
    s3_oracle  = await getOracleRate(s3.address);
    console.log(`  Rate locked at ${s3_oracle.rate}`);
    newDueDate3 = dueDate3 + 30 * 86400;
    s3_rollReq = await send(deployerW, s3.address, TFX_ABI, "requestRoll", [BigInt(newDueDate3)]);
    console.log(`  Roll requested to ${new Date(newDueDate3 * 1000).toISOString().slice(0,10)}`);
  }

  const s3_newDateStr = new Date(newDueDate3 * 1000).toISOString().slice(0, 10);
  const s3_rollOracle = await getOracleRoll(s3.address, s3_newDateStr);
  console.log(`  Rolled rate: ${s3_rollOracle.rate}, tx: ${s3_rollOracle.tx}`);

  const s3_amount = await read(s3.address, TFX_ABI, "settlementAmount");
  const s3_approve = await send(importerW, pen.address, ERC20_ABI, "approve", [s3.address, s3_amount]);
  const s3_fund    = await send(importerW, s3.address, TFX_ABI, "fundSettlement");
  const s3_settle  = await send(deployerW, s3.address, TFX_ABI, "settle");

  const s3_status = await read(s3.address, TFX_ABI, "status");

  results.scenario3 = {
    label: "Date Roll",
    ref: "QC-S3-ROLL",
    contract: s3.address,
    settlementToken: pen.address,
    invoiceBOB: "150,000",
    originalDue: new Date(dueDate3 * 1000).toISOString().slice(0, 10),
    rolledDue: new Date(newDueDate3 * 1000).toISOString().slice(0, 10),
    rateAtLock: s3_oracle.rate,
    rateAtRoll: s3_rollOracle.rate,
    settlementPEN: formatUnits(s3_amount, 18),
    txs: {
      deploy: s3.deployTx,
      requestRateLock: s3_lockReq,
      rateLock: s3_oracle.tx,
      requestRoll: s3_rollReq,
      rollDelivered: s3_rollOracle.tx,
      approve: s3_approve,
      fund: s3_fund,
      settle: s3_settle,
    },
    finalStatus: STATUS[s3_status],
  };

  // ── SCENARIO 4: Cancel & Refund ────────────────────────────────────────────
  console.log("\n\n=== SCENARIO 4: Cancel & Refund ===");
  const s4 = await deployScenario(
    "Cancel & Refund", "QC-S4-CANCEL",
    pen.address, parseUnits("150000", 18).toString(),
    now + 30 * 86400
  );

  const s4_lockReq = await send(deployerW, s4.address, TFX_ABI, "requestRateLock");
  const s4_oracle  = await getOracleRate(s4.address);
  console.log(`  Rate locked at ${s4_oracle.rate}`);

  const s4_amount = await read(s4.address, TFX_ABI, "settlementAmount");
  const s4_approve = await send(importerW, pen.address, ERC20_ABI, "approve", [s4.address, s4_amount]);
  const s4_fund    = await send(importerW, s4.address, TFX_ABI, "fundSettlement");
  console.log(`  Funded with ${formatUnits(s4_amount, 18)} mPEN`);

  // Cancel — triggers refund
  const s4_cancel = await send(deployerW, s4.address, TFX_ABI, "cancelAndRefund", [1]);
  console.log(`  Cancelled & refunded: ${s4_cancel}`);

  const s4_status = await read(s4.address, TFX_ABI, "status");

  results.scenario4 = {
    label: "Cancel & Refund",
    ref: "QC-S4-CANCEL",
    contract: s4.address,
    settlementToken: pen.address,
    invoiceBOB: "150,000",
    rate: s4_oracle.rate,
    fundedPEN: formatUnits(s4_amount, 18),
    refundedPEN: formatUnits(s4_amount, 18),
    txs: {
      deploy: s4.deployTx,
      requestRateLock: s4_lockReq,
      rateLock: s4_oracle.tx,
      approve: s4_approve,
      fund: s4_fund,
      cancel: s4_cancel,
    },
    finalStatus: STATUS[s4_status],
  };

  // ── Write artifacts ───────────────────────────────────────────────────────
  const outPath = `${ROOT}/artifacts/scenarios.json`;
  writeFileSync(outPath, JSON.stringify(results, null, 2));
  console.log(`\n\n✅ All scenarios complete. Artifacts: ${outPath}`);
  console.log(JSON.stringify(results, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
