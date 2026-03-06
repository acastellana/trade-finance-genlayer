// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title GenLayerForexOracle
/// @notice On-chain store for BOB/PEN exchange rates committed from a
///         GenLayer intelligent contract via an authorised relayer.
///
/// Architecture
/// ─────────────
///   [GenLayer Studionet]                [Base Sepolia]
///   ForexOracle.py  ──update_rate()──▶  GL AI validators reach consensus
///                                         │
///   off-chain relayer (forex-oracle.mjs)  │  reads rate_18 from GL
///                                         ▼
///                                   GenLayerForexOracle.commitRate(rate18)
///                                         │
///                                   TradeFinanceEscrow (reads getRate())
///
/// Trust model: the relayer is the only address allowed to push rates.
/// In production this would be the InternetCourt bridge contract itself.
/// For testnet the exporter/deployer acts as relayer.
///
/// IMockForexOracle-compatible: drop-in replacement in TradeFinanceEscrow.
contract GenLayerForexOracle {
    // ── State ──────────────────────────────────────────────────────────────
    address public immutable relayer;
    uint256 public rate18;           // PEN per BOB, 18-decimal fixed point
    uint256 public updatedAt;        // block.timestamp of last commit
    uint256 public updateCount;
    uint256 public maxAgeSeconds;    // stale-rate guard (default 24h)

    // ── Events ─────────────────────────────────────────────────────────────
    event RateCommitted(uint256 rate18, uint256 timestamp, uint256 updateCount);
    event MaxAgeUpdated(uint256 newMaxAge);

    // ── Constructor ────────────────────────────────────────────────────────
    /// @param _relayer   Address allowed to push rates (exporter wallet in demo)
    /// @param _maxAge    Seconds before rate is considered stale (0 = no check)
    constructor(address _relayer, uint256 _maxAge) {
        require(_relayer != address(0), "relayer required");
        relayer = _relayer;
        maxAgeSeconds = _maxAge == 0 ? 24 hours : _maxAge;
    }

    // ── Relayer API ────────────────────────────────────────────────────────
    /// @notice Push a new rate. Only callable by the authorised relayer.
    /// @param _rate18  PEN per BOB with 18 decimal places
    ///                 e.g. 0.4948 BOB/PEN → 494800000000000000
    function commitRate(uint256 _rate18) external {
        require(msg.sender == relayer, "GenLayerForexOracle: not relayer");
        require(_rate18 > 0, "GenLayerForexOracle: zero rate");
        // Sanity: 0.10 ≤ rate ≤ 5.00 in 18-decimal fixed point
        require(_rate18 >= 0.10e18 && _rate18 <= 5.00e18, "GenLayerForexOracle: rate out of range");

        rate18 = _rate18;
        updatedAt = block.timestamp;
        updateCount++;
        emit RateCommitted(_rate18, block.timestamp, updateCount);
    }

    // ── IMockForexOracle interface ─────────────────────────────────────────
    /// @return PEN per BOB, 18-decimal fixed point
    function getRate() external view returns (uint256) {
        require(rate18 > 0, "GenLayerForexOracle: no rate committed yet");
        if (maxAgeSeconds > 0 && updatedAt > 0) {
            require(
                block.timestamp - updatedAt <= maxAgeSeconds,
                "GenLayerForexOracle: rate is stale"
            );
        }
        return rate18;
    }

    // ── View helpers ───────────────────────────────────────────────────────
    function isStale() external view returns (bool) {
        if (rate18 == 0) return true;
        if (maxAgeSeconds == 0) return false;
        return block.timestamp - updatedAt > maxAgeSeconds;
    }

    function rateInfo() external view returns (
        uint256 _rate18,
        uint256 _updatedAt,
        uint256 _updateCount,
        bool _isStale
    ) {
        bool stale = rate18 == 0 || (maxAgeSeconds > 0 && block.timestamp - updatedAt > maxAgeSeconds);
        return (rate18, updatedAt, updateCount, stale);
    }
}
