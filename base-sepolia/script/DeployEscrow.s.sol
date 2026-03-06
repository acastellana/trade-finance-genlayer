// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/TradeFinanceEscrow.sol";

/// @title DeployEscrow — Deploy a TradeFinanceEscrow using existing token contracts
/// @dev Env vars: DEPLOYER_KEY, EXPORTER_ADDR, IMPORTER_ADDR, SBOB_ADDR, SPEN_ADDR
///               FOREX_RATE_18 (default 0.4948), FOREX_ORACLE (default 0), INVOICE_DESC
contract DeployEscrow is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_KEY");
        address exporter    = vm.envAddress("EXPORTER_ADDR");
        address importer    = vm.envAddress("IMPORTER_ADDR");
        address sBOB        = vm.envAddress("SBOB_ADDR");
        address sPEN        = vm.envAddress("SPEN_ADDR");
        string memory desc  = vm.envOr("INVOICE_DESC", string(
            "50 MT battery-grade Li2CO3 (ISO 6206:2023). CIP Callao. PO EP-PO-2026-0219."
        ));
        uint256 forexRate18 = vm.envOr("FOREX_RATE_18", uint256(494_800_000_000_000_000));
        address forexOracle = vm.envOr("FOREX_ORACLE",  address(0));
        uint256 invoiceBOB  = vm.envOr("INVOICE_BOB",  uint256(500_000e18));

        vm.startBroadcast(deployerKey);

        TradeFinanceEscrow escrow = new TradeFinanceEscrow(
            exporter,
            importer,
            sBOB,
            sPEN,
            invoiceBOB,
            forexOracle,
            forexRate18,
            desc
        );

        vm.stopBroadcast();

        console.log("TradeFinanceEscrow deployed at:", address(escrow));
        console.log("Locked forex rate (e18):       ", forexRate18);
        uint256 implied = (invoiceBOB * forexRate18) / 1e18;
        console.log("Implied settlement (sPEN):     ", implied / 1e18);
        console.log("Status:", uint8(escrow.status()));
    }
}
