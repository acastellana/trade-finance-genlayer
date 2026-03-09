#!/usr/bin/env node
/**
 * Run Scenario G2 (VERY_LATE with 60s return proof window) through full lifecycle,
 * then trigger timeoutReturnProof() to settle at 98.5%.
 */
import {
  createPublicClient, createWalletClient, http,
  parseUnits, parseAbi, formatUnits
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";
import { readFileSync } from "fs";

const RPC = "https://sepolia.base.org";
const CONTRACT = "0x2d14F1Ad43D90e8db1209A6cE3c1959c85b4D9e1";
const MOCK_PEN = "0x08bc87f6511913caa4e127c5e4e91618a37a9719";
const ESCROW = parseUnits("73950", 18);
const RATE = parseUnits("0.493", 18);

const SHEET_A = "QmbkgbCx3qQASGmFufJk1TMY76hJFgUrd6CjcZbxp7eVCu";
const SHEET_B = "QmQH94aKszWKkEqyWzu7411Ke6yQN9aKgQNbeCqYzhfmsu";
const STATEMENT = "Shipment under Contract ISPA-2025-BOL-PER-0047 crossed Bolivian export customs at Desaguadero on or before 2026-04-05T23:59:59-04:00.";
const GUIDELINE = "shipment-deadline-v1";

function loadKey(p) { const k = readFileSync(p, "utf8").trim(); return k.startsWith("0x") ? k : "0x" + k; }
const EXPORTER_KEY = loadKey(`${process.env.HOME}/.internetcourt/.exporter_key`);
const IMPORTER_KEY = loadKey(`${process.env.HOME}/.internetcourt/.importer_key`);
const RELAYER_KEY = loadKey("/home/albert/clawd/projects/conditional-payment-cross-border-trade/base-sepolia/.wallets/relayer.key");

const transport = http(RPC);
const pub = createPublicClient({ chain: baseSepolia, transport });
const exporterW = createWalletClient({ chain: baseSepolia, transport, account: privateKeyToAccount(EXPORTER_KEY) });
const importerW = createWalletClient({ chain: baseSepolia, transport, account: privateKeyToAccount(IMPORTER_KEY) });
const relayerW = createWalletClient({ chain: baseSepolia, transport, account: privateKeyToAccount(RELAYER_KEY) });

const ERC20 = parseAbi(["function approve(address,uint256) returns (bool)", "function balanceOf(address) view returns (uint256)"]);
const TFX = parseAbi([
  "function requestRateLock()",
  "function receiveRate(uint256,bytes32,bytes32,uint256)",
  "function fundSettlement()",
  "function contestShipment(string,string,string,string)",
  "function timeoutReturnProof()",
  "function status() view returns (uint8)",
  "function shipmentStatus() view returns (uint8)",
  "function fundedAmount() view returns (uint256)",
  "function returnProofDeadline() view returns (uint256)",
]);

async function tx(w, addr, abi, fn, args, label) {
  const h = await w.writeContract({ address: addr, abi, functionName: fn, args });
  const r = await pub.waitForTransactionReceipt({ hash: h, timeout: 60000 });
  if (r.status !== "success") throw new Error(`${label} reverted`);
  console.log(`  ✅ ${label}: ${h}`);
  return h;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  console.log(`\n=== G2: VERY_LATE with 60s return proof window ===`);
  console.log(`Contract: ${CONTRACT}\n`);

  // 1. Rate lock
  console.log("[1] requestRateLock...");
  await tx(exporterW, CONTRACT, TFX, "requestRateLock", [], "requestRateLock");
  await sleep(3000);

  // 2. Receive rate
  console.log("[2] receiveRate...");
  await tx(relayerW, CONTRACT, TFX, "receiveRate", [
    RATE,
    "0x4243525042434243524f5353000000000000000000000000000000000000000",
    "0x514332303236303030310000000000000000000000000000000000000000000",
    BigInt(Math.floor(Date.now() / 1000))
  ], "receiveRate");
  await sleep(3000);

  // 3. Approve + fund
  console.log("[3] approve + fundSettlement...");
  await tx(importerW, MOCK_PEN, ERC20, "approve", [CONTRACT, ESCROW], "approve");
  await sleep(3000);
  await tx(importerW, CONTRACT, TFX, "fundSettlement", [], "fundSettlement");
  await sleep(3000);

  // 4. Contest
  console.log("[4] contestShipment...");
  const contestTx = await tx(importerW, CONTRACT, TFX, "contestShipment", [SHEET_A, SHEET_B, STATEMENT, GUIDELINE], "contestShipment");

  let s = await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "shipmentStatus" });
  console.log(`\n  shipmentStatus: ${s} (2=CONTESTED)`);

  // 5. Wait for GenLayer verdict via relay
  console.log("\n[5] Waiting for GenLayer verdict delivery via bridge...");
  console.log("    (relay must be running — checking every 30s)");
  for (let i = 0; i < 40; i++) {
    await sleep(30000);
    s = Number(await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "shipmentStatus" }));
    const st = Number(await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "status" }));
    console.log(`    [${(i+1)*30}s] status=${st} shipmentStatus=${s}`);
    if (s === 6) { // RETURN_REQUIRED
      console.log("    ✅ RETURN_REQUIRED reached!");
      break;
    }
    if (st === 6) { // SETTLED (shouldn't happen for VERY_LATE but just in case)
      console.log("    ⚠️ Already settled?");
      break;
    }
  }

  // 6. Check return proof deadline
  const deadline = Number(await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "returnProofDeadline" }));
  const now = Math.floor(Date.now() / 1000);
  const wait = deadline - now;
  console.log(`\n[6] Return proof deadline: ${deadline} (in ${wait}s)`);

  if (wait > 0) {
    console.log(`    Waiting ${wait + 5}s for window to expire...`);
    await sleep((wait + 5) * 1000);
  }

  // 7. Timeout
  console.log("\n[7] timeoutReturnProof()...");
  const timeoutTx = await tx(exporterW, CONTRACT, TFX, "timeoutReturnProof", [], "timeoutReturnProof");

  // 8. Final state
  const finalStatus = Number(await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "status" }));
  const finalShipment = Number(await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "shipmentStatus" }));
  const finalFunded = await pub.readContract({ address: CONTRACT, abi: TFX, functionName: "fundedAmount" });

  console.log(`\n=== FINAL STATE ===`);
  console.log(`  status: ${finalStatus} (6=SETTLED)`);
  console.log(`  shipmentStatus: ${finalShipment}`);
  console.log(`  fundedAmount: ${formatUnits(finalFunded, 18)} PEN`);
  console.log(`  timeoutReturnProof tx: ${timeoutTx}`);
  console.log(`  Expected: 98.5% to exporter (72,840.75 PEN), 1.5% to importer (1,109.25 PEN)`);
})().catch(e => { console.error("❌", e); process.exit(1); });
