// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/TradeFxSettlement.sol";

/**
 * @title DeployTradeFx
 * @notice Deploys a TradeFxSettlement contract wired into InternetCourtFactory.
 *
 * Required env vars:
 *   DEPLOYER_KEY        Private key of the deployer (hex uint256)
 *   EXPORTER_ADDR       Exporter wallet address
 *   IMPORTER_ADDR       Importer wallet address
 *   ORACLE_RELAYER_ADDR Address that calls receiveRate()
 *
 * Optional env vars:
 *   ADMIN_ADDR          Admin (default: deployer)
 *   SETTLEMENT_TOKEN    MockPEN address
 *   BRIDGE_RECEIVER     InternetCourt BridgeReceiver on Base Sepolia
 *   COURT_FACTORY       InternetCourtFactory on Base Sepolia (enables IC integration)
 *   INVOICE_BOB         Invoice amount in BOB × 10^18 (default: 150_000e18)
 *   INVOICE_REF         Off-chain invoice reference string
 *   DUE_DATE_UNIX       Expected payment date as unix timestamp (default: 90 days)
 *   SOURCE_CURRENCY     bytes32 ISO code (default: "BOB")
 *   SETTLEMENT_CURRENCY bytes32 ISO code (default: "PEN")
 */
contract DeployTradeFx is Script {

    function run() external {
        uint256 deployerKey  = vm.envUint("DEPLOYER_KEY");
        address exporter     = vm.envAddress("EXPORTER_ADDR");
        address importer     = vm.envAddress("IMPORTER_ADDR");
        address relayer      = vm.envAddress("ORACLE_RELAYER_ADDR");

        address admin        = vm.envOr("ADMIN_ADDR",          address(0));
        address tokenAddr    = vm.envOr("SETTLEMENT_TOKEN",    address(0));
        address bridgeRcvr   = vm.envOr("BRIDGE_RECEIVER",     address(0));
        address courtFactory = vm.envOr("COURT_FACTORY",       address(0));
        uint256 invoiceAmt   = vm.envOr("INVOICE_BOB",         uint256(150_000e18));
        string  memory ref   = vm.envOr("INVOICE_REF",         string("QC-COOP-2026-0001"));
        uint256 dueDate      = vm.envOr("DUE_DATE_UNIX",       block.timestamp + 90 days);
        bytes32 srcCcy       = vm.envOr("SOURCE_CURRENCY",     bytes32(bytes("BOB")));
        bytes32 stlCcy       = vm.envOr("SETTLEMENT_CURRENCY", bytes32(bytes("PEN")));

        vm.startBroadcast(deployerKey);

        TradeFxSettlement settlement = new TradeFxSettlement(
            exporter,
            importer,
            relayer,
            admin,
            tokenAddr,
            invoiceAmt,
            srcCcy,
            stlCcy,
            dueDate,
            ref,
            bridgeRcvr,
            courtFactory
        );

        vm.stopBroadcast();

        address deployer = vm.addr(deployerKey);
        console.log("=== TradeFxSettlement deployed ===");
        console.log("Address:       ", address(settlement));
        console.log("Chain ID:      ", block.chainid);
        console.log("Exporter:      ", exporter);
        console.log("Importer:      ", importer);
        console.log("Oracle relayer:", relayer);
        console.log("Court factory: ", courtFactory);
        console.log("Invoice ref:   ", ref);

        string memory json = string(abi.encodePacked(
            '{"contract":"', vm.toString(address(settlement)),
            '","deployer":"', vm.toString(deployer),
            '","relayer":"', vm.toString(relayer),
            '","exporter":"', vm.toString(exporter),
            '","importer":"', vm.toString(importer),
            '","courtFactory":"', vm.toString(courtFactory),
            '","chainId":', vm.toString(block.chainid),
            ',"invoiceRef":"', ref, '"}'
        ));
        vm.writeFile("artifacts/trade-fx-base-deployment.json", json);
        console.log("Manifest: artifacts/trade-fx-base-deployment.json");
    }
}
