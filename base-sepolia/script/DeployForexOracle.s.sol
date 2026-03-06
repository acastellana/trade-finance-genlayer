// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/GenLayerForexOracle.sol";

/**
 * @title DeployForexOracle
 * @notice Deploys GenLayerForexOracle on Base Sepolia, then pushes the
 *         rate fetched by the GenLayer ForexOracle.py intelligent contract.
 *
 * Typical usage (after running `node scripts/forex-oracle.mjs run`):
 *
 *   export RELAYER_KEY=$(cat ~/.internetcourt/.exporter_key)
 *   export FOREX_RATE_18=<rate_18 from GenLayer oracle>
 *   export PATH="$HOME/.foundry/bin:$PATH"
 *   forge script script/DeployForexOracle.s.sol \
 *     --rpc-url https://sepolia.base.org \
 *     --broadcast -vvv
 *
 * The script prints the oracle address to embed in the next escrow deploy.
 */
contract DeployForexOracle is Script {
    function run() external {
        uint256 relayerKey = uint256(vm.envBytes32("RELAYER_KEY"));
        address relayer = vm.addr(relayerKey);
        uint256 rate18  = vm.envUint("FOREX_RATE_18");

        require(rate18 > 0, "DeployForexOracle: set FOREX_RATE_18");
        require(
            rate18 >= 0.10e18 && rate18 <= 5.00e18,
            "DeployForexOracle: rate out of range [0.10, 5.00]"
        );

        vm.startBroadcast(relayerKey);

        // 24-hour staleness guard
        GenLayerForexOracle oracle = new GenLayerForexOracle(relayer, 24 hours);

        // Immediately commit the GenLayer-sourced rate
        oracle.commitRate(rate18);

        vm.stopBroadcast();

        console.log("=== GenLayerForexOracle deployed ===");
        console.log("Address:    ", address(oracle));
        console.log("Relayer:    ", relayer);
        console.log("rate18:     ", rate18);
        console.log("rate (human):", _formatRate(rate18));
        console.log("");
        console.log("Next: set FOREX_ORACLE=", address(oracle));
        console.log("      forge script script/Deploy.s.sol ...");
    }

    function _formatRate(uint256 r) internal pure returns (string memory) {
        // Returns e.g. "0.4948" from 494800000000000000
        uint256 whole = r / 1e18;
        uint256 frac  = (r % 1e18) * 10000 / 1e18; // 4 decimal places
        return string(abi.encodePacked(
            vm.toString(whole), ".",
            frac < 1000 ? "0" : "",
            frac < 100  ? "0" : "",
            frac < 10   ? "0" : "",
            vm.toString(frac)
        ));
    }
}
