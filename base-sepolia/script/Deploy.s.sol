// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/Stablecoin.sol";
import "../src/TradeFinanceEscrow.sol";

/// @title Deploy — Deploys (or reuses) sBOB/sPEN and creates a TradeFinanceEscrow
///
/// Environment variables:
///   Required:
///     DEPLOYER_KEY      — private key of the deployer (exporter in the demo)
///     EXPORTER_ADDR     — exporter wallet address
///     IMPORTER_ADDR     — importer wallet address
///
///   Optional (token reuse — avoids fresh deployments for each case):
///     SBOB_ADDR         — existing sBOB contract; if unset, deploys new one
///     SPEN_ADDR         — existing sPEN contract; if unset, deploys new one
///
///   Optional (forex rate):
///     FOREX_RATE_18     — PEN/BOB rate as uint256 with 18 dec (default: 494800000000000000 = 0.4948)
///     FOREX_ORACLE      — oracle contract address (default: address(0), uses FOREX_RATE_18)
///
///   Optional (deal parameters):
///     INVOICE_BOB       — invoice in BOB wei (default: 500_000e18)
///     INCOTERMS         — e.g. "CIP Callao" (default: "CIP Callao")
///     PO_NUMBER         — purchase order ref (default: "EP-PO-2026-0219")
///     VESSEL            — vessel/B-L description (default: "TBD at shipment")
///
contract Deploy is Script {
    function run() external {
        uint256 deployerKey  = vm.envUint("DEPLOYER_KEY");
        address exporter     = vm.envAddress("EXPORTER_ADDR");
        address importer     = vm.envAddress("IMPORTER_ADDR");

        // Forex rate locked at escrow creation (0.4948 PEN/BOB default)
        uint256 forexRate18  = vm.envOr("FOREX_RATE_18", uint256(494_800_000_000_000_000));
        address forexOracle  = vm.envOr("FOREX_ORACLE",  address(0));

        // Invoice amount
        uint256 invoiceBOB   = vm.envOr("INVOICE_BOB", uint256(500_000e18));

        // Trade terms
        string memory incoterms = vm.envOr("INCOTERMS", string("CIP Callao"));
        string memory po        = vm.envOr("PO_NUMBER",  string("EP-PO-2026-0219"));

        // Optional: reuse existing token contracts
        address sBOBAddr     = vm.envOr("SBOB_ADDR", address(0));
        address sPENAddr     = vm.envOr("SPEN_ADDR", address(0));

        vm.startBroadcast(deployerKey);

        Stablecoin sBOB;
        Stablecoin sPEN;

        if (sBOBAddr != address(0)) {
            sBOB = Stablecoin(sBOBAddr);
            console.log("Reusing sBOB at:", sBOBAddr);
        } else {
            sBOB = new Stablecoin("Synthetic Boliviano", "sBOB", 18);
            // Mint invoice amount to exporter
            sBOB.mint(exporter, invoiceBOB);
            console.log("sBOB deployed at:", address(sBOB));
        }

        if (sPENAddr != address(0)) {
            sPEN = Stablecoin(sPENAddr);
            console.log("Reusing sPEN at:", sPENAddr);
        } else {
            sPEN = new Stablecoin("Synthetic Sol", "sPEN", 18);
            // Mint enough for settlement + buffer (invoice * rate * 1.05 buffer)
            uint256 settlementPEN = (invoiceBOB * forexRate18) / 1e18;
            uint256 mintPEN = (settlementPEN * 105) / 100; // 5% buffer
            sPEN.mint(importer, mintPEN);
            console.log("sPEN deployed at:", address(sPEN));
            console.log("Importer sPEN minted:", mintPEN);
        }

        // Build description — CIP incoterms explicitly stated
        // Arbitration: ICC Arbitration Rules superseded by InternetCourt if opened
        string memory desc = string(abi.encodePacked(
            "50 MT battery-grade Li2CO3 (ISO 6206:2023 purity>=99.5% moisture<=0.2%). ",
            "Minera Andina SRL (Bolivia, Antofagasta) to Electroquimica del Peru SA (Peru, Callao). ",
            "Incoterms 2020: ", incoterms, ". PO: ", po, ". ",
            "Forex: PEN/BOB rate locked at deal creation. ",
            "Dispute: ICC Arbitration Rules (Art.6), superseded by InternetCourt if created on-chain."
        ));

        TradeFinanceEscrow escrow = new TradeFinanceEscrow(
            exporter,
            importer,
            address(sBOB),
            address(sPEN),
            invoiceBOB,
            forexOracle,
            forexRate18,
            desc
        );

        vm.stopBroadcast();

        console.log("=== Deployment complete ===");
        console.log("sBOB:              ", address(sBOB));
        console.log("sPEN:              ", address(sPEN));
        console.log("TradeFinanceEscrow:", address(escrow));
        console.log("Exporter:          ", exporter);
        console.log("Importer:          ", importer);
        console.log("Invoice (BOB):     ", invoiceBOB / 1e18);
        console.log("Forex rate (e18):  ", forexRate18);
        uint256 implied = (invoiceBOB * forexRate18) / 1e18;
        console.log("Implied settlement:", implied / 1e18, "sPEN");
        console.log("Incoterms:         ", incoterms);
        console.log("PO:                ", po);
    }
}
