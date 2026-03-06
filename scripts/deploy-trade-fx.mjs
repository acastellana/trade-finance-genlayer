#!/usr/bin/env node
/**
 * deploy-trade-fx.mjs
 *
 * Full deployment orchestrator for the TradeFxSettlement prototype.
 *
 * Steps:
 *   1. Validate env and prerequisites
 *   2. Deploy FxBenchmarkOracle on GenLayer Studionet (or reuse existing)
 *   3. Deploy TradeFxSettlement on Base Sepolia (Foundry)
 *   4. Write machine-readable deployment manifest (artifacts/deployment.json)
 *      → fed directly into frontend config
 *
 * Usage:
 *   node scripts/deploy-trade-fx.mjs
 *   node scripts/deploy-trade-fx.mjs --reuse-oracle <glOracleAddress>
 *   node scripts/deploy-trade-fx.mjs --dry-run      (validate only, no deploy)
 *
 * Required env vars:
 *   DEPLOYER_KEY        Base Sepolia deployer private key (hex, 0x-prefixed or raw)
 *   RELAYER_KEY         Base Sepolia relayer private key  (hex, 0x-prefixed)
 *                       The address derived from this key becomes oracleRelayer.
 *   GL_PRIVATE_KEY      GenLayer account private key
 *   EXPORTER_ADDR       Exporter wallet address (Base Sepolia)
 *   IMPORTER_ADDR       Importer wallet address (Base Sepolia)
 *
 * Optional:
 *   ADMIN_ADDR          Exception admin (default: deployer address)
 *   INVOICE_BOB         Invoice in BOB × 10^18 (default: 150000000000000000000000)
 *   INVOICE_REF         Off-chain invoice reference (default: QC-COOP-2026-0001)
 *   PAYMENT_DAYS        Days until expected payment (default: 30)
 *   BASE_SEPOLIA_RPC    Default: https://sepolia.base.org
 */

import { createClient, createAccount } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import { execSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname  = path.dirname(fileURLToPath(import.meta.url));
const ROOT       = path.join(__dirname, "..");
const ARTIFACTS  = path.join(ROOT, "artifacts");
const GL_ORACLE_CONTRACT = path.join(ROOT, "contracts", "FxBenchmarkOracle.py");
const FOUNDRY_DIR = path.join(ROOT, "base-sepolia");
const CAST       = process.env.CAST_PATH || `${process.env.HOME}/.foundry/bin/cast`;
const FORGE      = process.env.FORGE_PATH || `${process.env.HOME}/.foundry/bin/forge`;
const BASE_RPC   = process.env.BASE_SEPOLIA_RPC || "https://sepolia.base.org";
const GL_RPC     = "https://studio.genlayer.com/api";
const CHAIN_ID   = 84532;  // Base Sepolia

const DRY_RUN    = process.argv.includes("--dry-run");
const REUSE_IDX  = process.argv.indexOf("--reuse-oracle");
const REUSE_ADDR = REUSE_IDX > -1 ? process.argv[REUSE_IDX + 1] : null;

// ─── Logging ─────────────────────────────────────────────────────────────────

function ts() { return new Date().toISOString().replace("T", " ").split(".")[0]; }
function log(step, msg) { console.log(`[${ts()}] [${step}] ${msg}`); }
function section(title) { console.log(`\n${"─".repeat(60)}\n  ${title}\n${"─".repeat(60)}`); }

// ─── Env validation ───────────────────────────────────────────────────────────

function validateEnv() {
  const required = ["DEPLOYER_KEY", "RELAYER_KEY", "GL_PRIVATE_KEY", "EXPORTER_ADDR", "IMPORTER_ADDR"];
  const missing  = required.filter(k => !process.env[k]);
  if (missing.length) {
    throw new Error(`Missing required env vars: ${missing.join(", ")}`);
  }

  // Check foundry tools
  for (const bin of [CAST, FORGE]) {
    if (!fs.existsSync(bin)) throw new Error(`Not found: ${bin}`);
  }

  // Check oracle contract source
  if (!fs.existsSync(GL_ORACLE_CONTRACT)) {
    throw new Error(`FxBenchmarkOracle.py not found at ${GL_ORACLE_CONTRACT}`);
  }

  log("validate", "All prerequisites OK");
}

/**
 * Derive an Ethereum address from a raw private key (hex, with or without 0x).
 */
function deriveAddress(pk) {
  const key = pk.startsWith("0x") ? pk : "0x" + pk;
  return execSync(`${CAST} wallet address --private-key ${key}`, { encoding: "utf8" }).trim();
}

// ─── Step 1: Deploy FxBenchmarkOracle on GenLayer ────────────────────────────

async function deployGLOracle() {
  section("STEP 1: Deploy FxBenchmarkOracle on GenLayer Studionet");

  const account = createAccount(process.env.GL_PRIVATE_KEY);
  const client  = createClient({ chain: studionet, account });

  // Fund account from faucet
  try {
    await fetch(`${GL_RPC}/faucet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: account.address }),
    });
    log("genlayer", `Faucet requested for ${account.address}`);
  } catch (_) {}

  await client.initializeConsensusSmartContract();

  const code = fs.readFileSync(GL_ORACLE_CONTRACT, "utf8");
  log("genlayer", "Deploying FxBenchmarkOracle (tolerance_bps=200)…");
  const hash = await client.deployContract({
    code,
    args: [200],  // 200 bps max source spread
    leaderOnly: false,
  });

  log("genlayer", `Deploy tx: ${hash}`);
  log("genlayer", `Explorer: https://explorer-studio.genlayer.com/transactions/${hash}`);
  log("genlayer", "Waiting for consensus (~60-120s)…");

  const receipt = await client.waitForTransactionReceipt({
    hash,
    status:   TransactionStatus.ACCEPTED,
    retries:  180,
    interval: 5000,
  });

  const address = receipt.data?.contract_address || receipt.to_address;
  if (!address) throw new Error("Could not extract oracle address from receipt");

  log("genlayer", `FxBenchmarkOracle deployed at: ${address}`);
  return address;
}

// ─── Step 2: Deploy TradeFxSettlement on Base Sepolia ────────────────────────

function deployBaseSepolia(relayerAddress) {
  section("STEP 2: Deploy TradeFxSettlement on Base Sepolia");

  const invoiceBob   = process.env.INVOICE_BOB  || "150000000000000000000000";  // 150,000 BOB
  const invoiceRef   = process.env.INVOICE_REF  || "QC-COOP-2026-0001";
  const paymentDays  = parseInt(process.env.PAYMENT_DAYS || "30", 10);
  const dueDateUnix  = Math.floor(Date.now() / 1000) + paymentDays * 86400;

  const exporter     = process.env.EXPORTER_ADDR;
  const importer     = process.env.IMPORTER_ADDR;
  const deployerKey  = process.env.DEPLOYER_KEY;
  const adminAddr    = process.env.ADMIN_ADDR   || "0x0000000000000000000000000000000000000000";

  log("forge", `Exporter:       ${exporter}`);
  log("forge", `Importer:       ${importer}`);
  log("forge", `Oracle relayer: ${relayerAddress}  ← derived from RELAYER_KEY`);
  log("forge", `Invoice:        ${(BigInt(invoiceBob) / BigInt(1e18)).toString()} BOB`);
  log("forge", `Invoice ref:    ${invoiceRef}`);
  log("forge", `Due date:       ${new Date(dueDateUnix * 1000).toISOString().split("T")[0]} (${dueDateUnix})`);

  // Ensure artifacts dir exists for manifest output
  fs.mkdirSync(path.join(FOUNDRY_DIR, "artifacts"), { recursive: true });
  fs.mkdirSync(ARTIFACTS, { recursive: true });

  const env = {
    ...process.env,
    DEPLOYER_KEY:         deployerKey.startsWith("0x") ? deployerKey : "0x" + deployerKey,
    EXPORTER_ADDR:        exporter,
    IMPORTER_ADDR:        importer,
    ORACLE_RELAYER_ADDR:  relayerAddress,
    ADMIN_ADDR:           adminAddr,
    INVOICE_BOB:          invoiceBob,
    INVOICE_REF:          invoiceRef,
    DUE_DATE_UNIX:        dueDateUnix.toString(),
    SOURCE_CURRENCY:      "BOB",
    SETTLEMENT_CURRENCY:  "PEN",
  };

  const cmd = [
    FORGE, "script", "script/DeployTradeFx.s.sol",
    "--rpc-url", BASE_RPC,
    "--broadcast",
    "--sig", '"run()"',
    "-vvv",
  ].join(" ");

  log("forge", `Running: ${cmd}`);
  const output = execSync(cmd, { cwd: FOUNDRY_DIR, env, encoding: "utf8" });
  console.log(output);

  // Read the manifest written by the Foundry script
  const manifestPath = path.join(FOUNDRY_DIR, "artifacts", "trade-fx-base-deployment.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error("Foundry script did not write artifacts/trade-fx-base-deployment.json");
  }
  const baseDeployment = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  log("forge", `TradeFxSettlement deployed at: ${baseDeployment.contract}`);
  return { baseDeployment, invoiceBob, invoiceRef, dueDateUnix };
}

// ─── Step 3: Write deployment manifest ───────────────────────────────────────

function writeManifest(glOracleAddress, baseDeployment, invoiceBob, invoiceRef, dueDateUnix) {
  section("STEP 3: Write deployment manifest");

  const dueDateIso  = new Date(dueDateUnix * 1000).toISOString().split("T")[0];
  const deployedAt  = new Date().toISOString();
  const invoiceHuman = (BigInt(invoiceBob) / BigInt(1e18)).toString();

  const manifest = {
    version:   "1.0.0",
    product:   "TradeFxSettlement",
    deployedAt,
    benchmark: {
      primary:  "BCRP_BCB_CROSS",
      fallback: "MARKET_AGGREGATE",
      description: "BOB/PEN = BCRP PEN/USD (series PD04638PD) / BCB 6.96 BOB/USD peg",
    },
    network: {
      baseSepolia: {
        chainId: CHAIN_ID,
        rpc:     BASE_RPC,
        contracts: {
          TradeFxSettlement: baseDeployment.contract,
        },
      },
      genLayerStudionet: {
        rpc: GL_RPC,
        contracts: {
          FxBenchmarkOracle: glOracleAddress,
        },
      },
    },
    roles: {
      deployer:      baseDeployment.deployer,
      exporter:      baseDeployment.exporter,
      importer:      baseDeployment.importer,
      oracleRelayer: baseDeployment.relayer,
    },
    trade: {
      invoiceRef,
      invoiceAmount:     invoiceBob,
      invoiceAmountHuman: invoiceHuman + " BOB",
      sourceCurrency:    "BOB",
      settlementCurrency: "PEN",
      expectedPaymentDate:      dueDateIso,
      expectedPaymentDateUnix:  dueDateUnix,
      scenario: "Bolivian quinoa cooperative (COOP-BOL-001) → Peruvian food manufacturer (PERU-MFG-001)",
    },
    relayer: {
      command:      "node scripts/fx-settlement-relayer.mjs watch <tradeAddress>",
      requiredEnv:  ["GL_PRIVATE_KEY", "RELAYER_KEY", "GL_ORACLE_ADDRESS", "TRADE_ADDRESS"],
      pollIntervalMs: 30000,
    },
  };

  const manifestPath = path.join(ARTIFACTS, "deployment.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  log("manifest", `Written: ${manifestPath}`);

  // Also write a .env snippet for convenience
  const envSnippet = [
    `# TradeFxSettlement deployment — ${deployedAt}`,
    `TRADE_ADDRESS=${baseDeployment.contract}`,
    `GL_ORACLE_ADDRESS=${glOracleAddress}`,
    `ORACLE_RELAYER_ADDR=${baseDeployment.relayer}`,
    `# Base Sepolia`,
    `BASE_SEPOLIA_RPC=${BASE_RPC}`,
  ].join("\n");

  const envPath = path.join(ARTIFACTS, "deployment.env");
  fs.writeFileSync(envPath, envSnippet);
  log("manifest", `Env snippet written: ${envPath}`);

  return manifest;
}

// ─── Summary ──────────────────────────────────────────────────────────────────

function printSummary(manifest) {
  const m = manifest;
  console.log(`
${"═".repeat(62)}
  DEPLOYMENT COMPLETE
${"═".repeat(62)}

  TradeFxSettlement:   ${m.network.baseSepolia.contracts.TradeFxSettlement}
  FxBenchmarkOracle:   ${m.network.genLayerStudionet.contracts.FxBenchmarkOracle}

  Benchmark:     ${m.benchmark.primary} (fallback: ${m.benchmark.fallback})
  Exporter:      ${m.roles.exporter}
  Importer:      ${m.roles.importer}
  Oracle relayer:${m.roles.oracleRelayer}

  Invoice:       ${m.trade.invoiceAmountHuman}  ref: ${m.trade.invoiceRef}
  Due date:      ${m.trade.expectedPaymentDate}
  Scenario:      ${m.trade.scenario}

  Manifest:      artifacts/deployment.json
  Env snippet:   artifacts/deployment.env

${"─".repeat(62)}
  NEXT STEPS
${"─".repeat(62)}

  1. Run a full rate lock:
     GL_ORACLE_ADDRESS=${m.network.genLayerStudionet.contracts.FxBenchmarkOracle} \\
     TRADE_ADDRESS=${m.network.baseSepolia.contracts.TradeFxSettlement} \\
     node scripts/fx-settlement-relayer.mjs lock ${m.network.baseSepolia.contracts.TradeFxSettlement}

     (First call TradeFxSettlement.requestRateLock() from exporter/importer wallet)

  2. Run a roll (after locking):
     node scripts/fx-settlement-relayer.mjs roll ${m.network.baseSepolia.contracts.TradeFxSettlement} 2026-05-06

  3. Start continuous watcher:
     node scripts/fx-settlement-relayer.mjs watch ${m.network.baseSepolia.contracts.TradeFxSettlement}

  4. Check trade status:
     node scripts/fx-settlement-relayer.mjs status ${m.network.baseSepolia.contracts.TradeFxSettlement}
${"═".repeat(62)}
`);
}

// ─── Entrypoint ───────────────────────────────────────────────────────────────

(async () => {
  try {
    console.log(`
${"═".repeat(62)}
  deploy-trade-fx.mjs
  TradeFxSettlement + FxBenchmarkOracle — Full Deploy
  ${DRY_RUN ? "DRY RUN — no transactions will be sent" : ""}
${"═".repeat(62)}
`);

    validateEnv();

    if (DRY_RUN) {
      const relayerAddr = deriveAddress(process.env.RELAYER_KEY);
      log("dry-run", `Relayer address (derived from RELAYER_KEY): ${relayerAddr}`);
      log("dry-run", "Validation passed. Exiting without deploying.");
      process.exit(0);
    }

    // Derive relayer address — this is the address that must call receiveRate()
    const relayerAddress = deriveAddress(process.env.RELAYER_KEY);
    log("setup", `Oracle relayer address: ${relayerAddress}`);

    // Step 1: GenLayer oracle
    let glOracleAddress;
    if (REUSE_ADDR) {
      log("genlayer", `Reusing existing oracle: ${REUSE_ADDR}`);
      glOracleAddress = REUSE_ADDR;
    } else {
      glOracleAddress = await deployGLOracle();
    }

    // Step 2: Base Sepolia contract
    const { baseDeployment, invoiceBob, invoiceRef, dueDateUnix } = deployBaseSepolia(relayerAddress);

    // Step 3: Write manifest
    const manifest = writeManifest(glOracleAddress, baseDeployment, invoiceBob, invoiceRef, dueDateUnix);

    printSummary(manifest);

  } catch (e) {
    console.error(`\n❌ Deploy failed: ${e.message || e}`);
    if (e.stack) console.error(e.stack);
    process.exit(1);
  }
})();
