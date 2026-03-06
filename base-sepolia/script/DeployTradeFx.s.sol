// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/TradeFxSettlement.sol";

/**
 * @title DeployTradeFx
 * @notice Deploys a TradeFxSettlement contract with explicit constructor wiring.
 *
 * All roles are set at deploy time. No post-deploy patching.
 *
 * Required env vars:
 *   DEPLOYER_KEY        Private key of the deployer (hex uint256)
 *   EXPORTER_ADDR       Exporter wallet address
 *   IMPORTER_ADDR       Importer wallet address
 *   ORACLE_RELAYER_ADDR Address that will call receiveRate() / receiveRolledRate()
 *                       Must match the wallet holding RELAYER_KEY in the relayer script.
 *
 * Optional env vars (trade parameters):
 *   ADMIN_ADDR          Admin for exception recovery (default: deployer)
 *   INVOICE_BOB         Invoice amount in BOB × 10^18 (default: 150_000e18 — quinoa demo)
 *   INVOICE_REF         Off-chain invoice reference string
 *   DUE_DATE_UNIX       Expected payment date as unix timestamp (default: 30 days from now)
 *   SOURCE_CURRENCY     3-letter ISO code, right-padded to bytes32 (default: "BOB")
 *   SETTLEMENT_CURRENCY 3-letter ISO code (default: "PEN")
 */
contract DeployTradeFx is Script {

    function run() external {
        // ── Required ────────────────────────────────────────────────────────
        uint256 deployerKey  = vm.envUint("DEPLOYER_KEY");
        address exporter     = vm.envAddress("EXPORTER_ADDR");
        address importer     = vm.envAddress("IMPORTER_ADDR");
        address relayer      = vm.envAddress("ORACLE_RELAYER_ADDR");

        // ── Optional ────────────────────────────────────────────────────────
        address admin        = vm.envOr("ADMIN_ADDR",  address(0));  // 0 → deployer
        address tokenAddr    = vm.envOr("SETTLEMENT_TOKEN", address(0));
        uint256 invoiceAmt   = vm.envOr("INVOICE_BOB", uint256(150_000e18));
        string  memory ref   = vm.envOr("INVOICE_REF", string("QC-COOP-2026-0001"));
        uint256 dueDate      = vm.envOr("DUE_DATE_UNIX", block.timestamp + 30 days);

        // Currency codes: right-pad short ASCII strings to bytes32
        bytes32 srcCcy  = vm.envOr("SOURCE_CURRENCY",      bytes32(bytes("BOB")));
        bytes32 stlCcy  = vm.envOr("SETTLEMENT_CURRENCY",  bytes32(bytes("PEN")));

        // ── Deploy ──────────────────────────────────────────────────────────
        vm.startBroadcast(deployerKey);

        TradeFxSettlement settlement = new TradeFxSettlement(
            exporter,
            importer,
            relayer,   // oracleRelayer — explicitly set, matches RELAYER_KEY wallet
            admin,
            tokenAddr, // settlementToken (MockPEN)
            invoiceAmt,
            srcCcy,
            stlCcy,
            dueDate,
            ref
        );

        vm.stopBroadcast();

        // ── Human-readable output ───────────────────────────────────────────
        address deployer = vm.addr(deployerKey);
        console.log("=== TradeFxSettlement deployed ===");
        console.log("Address:       ", address(settlement));
        console.log("Chain ID:      ", block.chainid);
        console.log("Deployer:      ", deployer);
        console.log("Exporter:      ", exporter);
        console.log("Importer:      ", importer);
        console.log("Oracle relayer:", relayer);
        console.log("Invoice (BOB): ", invoiceAmt / 1e18);
        console.log("Invoice ref:   ", ref);
        console.log("Due date unix: ", dueDate);
        console.log("Status:        ", uint8(settlement.status()));  // 0 = DRAFT

        // ── Machine-readable: write deployment manifest ─────────────────────
        // Consumed by deploy-trade-fx.mjs to build the full deployment summary.
        string memory json = string(abi.encodePacked(
            '{"contract":"', vm.toString(address(settlement)),
            '","deployer":"', vm.toString(deployer),
            '","relayer":"', vm.toString(relayer),
            '","exporter":"', vm.toString(exporter),
            '","importer":"', vm.toString(importer),
            '","chainId":', vm.toString(block.chainid),
            ',"invoiceRef":"', ref, '"}'
        ));
        vm.writeFile("artifacts/trade-fx-base-deployment.json", json);
        console.log("Manifest written: artifacts/trade-fx-base-deployment.json");
    }
}
