// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title IInternetCourtAgreement — Minimal interface for reading IC verdicts
interface IInternetCourtAgreement {
    function status() external view returns (uint8);
    function verdict() external view returns (uint8);
    function reasoning() external view returns (string memory);
}

/// @title IMockForexOracle — Rate oracle interface (Chainlink-style)
/// @dev In production, use Chainlink price feeds. Mock in tests/demo.
interface IMockForexOracle {
    /// @return rate18 PEN per BOB, 18-decimal fixed point
    function getRate() external view returns (uint256 rate18);
}

/// @title TradeFinanceEscrow v2
/// @notice CIP-incoterms escrow for Bolivia→Peru lithium carbonate trade.
///         Improvements over v1:
///         - Incoterms corrected: CIP (not CIF). Under CIP, seller bears
///           risk until goods are handed to the first carrier at destination
///           and insurance is provided in buyer's favour. Quality risk stays
///           with the seller until post-arrival inspection acceptance.
///         - Forex rate locked at escrow funding time via oracle, not at
///           settlement. Neither party can manipulate the rate.
///         - receiveUnderProtest() replaces the confirmDelivery+raiseDispute
///           antipattern. Importer receives goods but preserves dispute rights.
///         - Dispute can also be raised by exporter (e.g. non-payment).
///         - Shared token references passed in constructor; no re-deployment.
///         - Arbitration clause: ICC Arbitration Rules, seat La Paz/Lima TBD,
///           superseded by on-chain InternetCourt agreement if one is created.
///
/// Lifecycle (happy path):
///   CREATED → FUNDED → SHIPPED → DELIVERED → SETTLED
///
/// Lifecycle (dispute path):
///   CREATED → FUNDED → SHIPPED → PROTESTED → DISPUTED → RESOLVED
///   (importer calls receiveUnderProtest() instead of confirmDelivery())
///
/// Lifecycle (forced dispute after delivery):
///   CREATED → FUNDED → SHIPPED → DELIVERED → DISPUTED → RESOLVED
///   (either party can raise dispute within 5 days of delivery)

contract TradeFinanceEscrow {
    using SafeERC20 for IERC20;

    // ── Enums ──────────────────────────────────────────────────────────────
    enum Status {
        Created,    // 0 — deal created, awaiting funding
        Funded,     // 1 — importer deposited sPEN
        Shipped,    // 2 — exporter submitted B/L
        Delivered,  // 3 — importer confirmed receipt (no objection)
        Settled,    // 4 — payment released at locked forex rate
        Disputed,   // 5 — linked to InternetCourt case
        Resolved,   // 6 — IC verdict executed
        Protested,  // 7 — received under protest, pending inspection
        Cancelled   // 8 — cancelled before funding
    }

    // IC verdict constants (InternetCourt v0.2.0)
    // Exporter creates IC agreement → exporter = Party A
    uint8 constant IC_UNDETERMINED = 0;
    uint8 constant IC_PARTY_A      = 1; // Exporter wins: goods met spec
    uint8 constant IC_PARTY_B      = 2; // Importer wins: goods failed spec
    uint8 constant IC_RESOLVED     = 4;

    // Dispute window: 5 days after delivery confirmation
    uint256 constant DISPUTE_WINDOW = 5 days;
    // Inspection window: 7 days after receipt under protest
    uint256 constant INSPECTION_WINDOW = 7 days;

    // ── Immutables ─────────────────────────────────────────────────────────
    address public immutable exporter;       // Seller
    address public immutable importer;       // Buyer
    IERC20  public immutable invoiceToken;   // sBOB — invoice denomination
    IERC20  public immutable settlementToken; // sPEN — escrow + settlement currency
    uint256 public immutable invoiceAmountBOB; // Invoice in BOB (18 dec)
    uint256 public immutable lockedForexRate;   // PEN/BOB rate (18 dec), set at funding
    string  public description;               // Trade description incl. PO, incoterms

    // ── State ──────────────────────────────────────────────────────────────
    uint256 public escrowAmountPEN;        // sPEN deposited
    uint256 public settlementAmountPEN;    // Calculated from locked rate
    string  public shipmentRef;            // B/L reference
    Status  public status;

    uint256 public deliveredAt;            // Timestamp of delivery/protest
    bool    public exporterDisputed;       // Exporter triggered dispute
    bool    public importerDisputed;       // Importer triggered dispute

    // Dispute resolution
    address public courtCase;             // IC agreement address
    uint8   public courtVerdict;
    string  public courtReasoning;

    // ── Events ─────────────────────────────────────────────────────────────
    event DealCreated(address indexed exporter, address indexed importer,
                      uint256 invoiceAmountBOB, uint256 lockedForexRate);
    event EscrowFunded(address indexed importer, uint256 amountPEN,
                       uint256 lockedRate, uint256 impliedSettlementPEN);
    event ShipmentSubmitted(string ref);
    event DeliveryConfirmed();
    event ReceivedUnderProtest(address indexed importer, string reason);
    event DisputeRaised(address indexed by, address courtCase);
    event DealSettled(uint256 settlementAmountPEN, uint256 lockedRate18);
    event DisputeResolved(uint8 verdict, string reasoning);
    event FundsReleased(address indexed to, uint256 amount);
    event DealCancelled();

    // ── Modifiers ──────────────────────────────────────────────────────────
    modifier onlyExporter() { require(msg.sender == exporter, "only exporter"); _; }
    modifier onlyImporter() { require(msg.sender == importer, "only importer"); _; }
    modifier onlyParty()    { require(msg.sender == exporter || msg.sender == importer, "not a party"); _; }
    modifier inStatus(Status s) { require(status == s, string(abi.encodePacked("wrong status: expected ", uint8(s)))); _; }

    // ── Constructor ────────────────────────────────────────────────────────

    /// @param _exporter         Seller address
    /// @param _importer         Buyer address
    /// @param _invoiceToken     sBOB ERC-20 address
    /// @param _settlementToken  sPEN ERC-20 address
    /// @param _invoiceAmountBOB Invoice amount in BOB (18 dec)
    /// @param _forexOracle      Oracle providing PEN/BOB rate (address(0) = use _manualRate)
    /// @param _manualRate       Fallback rate if oracle is zero address (18 dec)
    /// @param _description      Human-readable description (incoterms, PO, goods)
    constructor(
        address _exporter,
        address _importer,
        address _invoiceToken,
        address _settlementToken,
        uint256 _invoiceAmountBOB,
        address _forexOracle,
        uint256 _manualRate,
        string memory _description
    ) {
        require(_exporter != address(0) && _importer != address(0), "zero address");
        require(_exporter != _importer, "same party");
        require(_invoiceAmountBOB > 0, "zero invoice");

        exporter = _exporter;
        importer = _importer;
        invoiceToken   = IERC20(_invoiceToken);
        settlementToken = IERC20(_settlementToken);
        invoiceAmountBOB = _invoiceAmountBOB;
        description = _description;
        status = Status.Created;

        // Lock the forex rate at contract creation (not at settlement)
        if (_forexOracle != address(0)) {
            lockedForexRate = IMockForexOracle(_forexOracle).getRate();
        } else {
            require(_manualRate > 0, "zero rate");
            lockedForexRate = _manualRate;
        }

        // Pre-compute expected settlement amount for transparency
        settlementAmountPEN = (invoiceAmountBOB * lockedForexRate) / 1e18;

        emit DealCreated(_exporter, _importer, _invoiceAmountBOB, lockedForexRate);
    }

    // ── Happy path ─────────────────────────────────────────────────────────

    /// @notice Importer deposits sPEN. Amount must cover implied settlement.
    /// @param amount sPEN to deposit (must be >= settlementAmountPEN)
    function fundEscrow(uint256 amount) external onlyImporter inStatus(Status.Created) {
        require(amount >= settlementAmountPEN, "escrow below settlement");
        settlementToken.safeTransferFrom(msg.sender, address(this), amount);
        escrowAmountPEN = amount;
        status = Status.Funded;
        emit EscrowFunded(msg.sender, amount, lockedForexRate, settlementAmountPEN);
    }

    /// @notice Exporter submits bill of lading or tracking reference.
    /// @param ref B/L number and vessel details
    function submitShipment(string calldata ref) external onlyExporter inStatus(Status.Funded) {
        require(bytes(ref).length > 0, "empty ref");
        shipmentRef = ref;
        status = Status.Shipped;
        emit ShipmentSubmitted(ref);
    }

    /// @notice Importer confirms receipt with no objection. Starts 5-day dispute window.
    function confirmDelivery() external onlyImporter inStatus(Status.Shipped) {
        status = Status.Delivered;
        deliveredAt = block.timestamp;
        emit DeliveryConfirmed();
    }

    /// @notice Settle at the forex rate locked at escrow creation.
    ///         Exporter receives settlementAmountPEN; remainder returned to importer.
    ///         Can only be called after DELIVERED and outside dispute window,
    ///         OR by either party after dispute window expires without a dispute.
    function settle() external inStatus(Status.Delivered) {
        // Within dispute window: only importer can settle (voluntary early release)
        // After dispute window: anyone can trigger (prevents deadlock)
        if (block.timestamp < deliveredAt + DISPUTE_WINDOW) {
            require(msg.sender == importer, "dispute window active");
        }

        settlementToken.safeTransfer(exporter, settlementAmountPEN);
        uint256 remainder = escrowAmountPEN - settlementAmountPEN;
        if (remainder > 0) {
            settlementToken.safeTransfer(importer, remainder);
        }
        status = Status.Settled;
        emit DealSettled(settlementAmountPEN, lockedForexRate);
        emit FundsReleased(exporter, settlementAmountPEN);
        if (remainder > 0) emit FundsReleased(importer, remainder);
    }

    // ── Dispute path ───────────────────────────────────────────────────────

    /// @notice Importer receives goods but disputes quality. Preserves all rights.
    ///         Use this instead of confirmDelivery() when inspection reveals issues.
    ///         Under CIP incoterms, post-arrival inspection is buyer's right.
    /// @param reason Short description of the objection
    function receiveUnderProtest(string calldata reason) external onlyImporter inStatus(Status.Shipped) {
        require(bytes(reason).length > 0, "empty reason");
        status = Status.Protested;
        deliveredAt = block.timestamp; // inspection window starts now
        emit ReceivedUnderProtest(msg.sender, reason);
    }

    /// @notice Link an InternetCourt agreement to resolve the dispute.
    ///         Can be called from DELIVERED (within dispute window) or PROTESTED.
    /// @param _courtCase Address of the IC agreement
    function raiseDispute(address _courtCase) external onlyParty {
        require(
            status == Status.Protested ||
            (status == Status.Delivered && block.timestamp < deliveredAt + DISPUTE_WINDOW),
            "cannot dispute: wrong status or window expired"
        );
        require(_courtCase != address(0), "zero address");
        courtCase = _courtCase;
        status = Status.Disputed;
        if (msg.sender == exporter) exporterDisputed = true;
        else importerDisputed = true;
        emit DisputeRaised(msg.sender, _courtCase);
    }

    /// @notice Read IC verdict and release funds accordingly.
    ///         Anyone can call once IC case is resolved.
    function resolveFromCourt() external inStatus(Status.Disputed) {
        require(courtCase != address(0), "no court case linked");
        IInternetCourtAgreement ic = IInternetCourtAgreement(courtCase);
        require(ic.status() == IC_RESOLVED, "court case not yet resolved");

        courtVerdict  = ic.verdict();
        courtReasoning = ic.reasoning();

        if (courtVerdict == IC_PARTY_A) {
            // Exporter (Party A) wins: goods met spec → exporter paid in full
            settlementToken.safeTransfer(exporter, escrowAmountPEN);
            emit FundsReleased(exporter, escrowAmountPEN);
        } else if (courtVerdict == IC_PARTY_B) {
            // Importer (Party B) wins: goods failed spec → full refund
            settlementToken.safeTransfer(importer, escrowAmountPEN);
            emit FundsReleased(importer, escrowAmountPEN);
        } else {
            // UNDETERMINED: split 50/50
            uint256 half = escrowAmountPEN / 2;
            settlementToken.safeTransfer(exporter, half);
            settlementToken.safeTransfer(importer, escrowAmountPEN - half);
            emit FundsReleased(exporter, half);
            emit FundsReleased(importer, escrowAmountPEN - half);
        }

        status = Status.Resolved;
        emit DisputeResolved(courtVerdict, courtReasoning);
    }

    /// @notice Cancel deal before funding
    function cancel() external onlyExporter inStatus(Status.Created) {
        status = Status.Cancelled;
        emit DealCancelled();
    }

    // ── View ───────────────────────────────────────────────────────────────

    function getDealInfo() external view returns (
        Status   _status,
        address  _exporter,
        address  _importer,
        uint256  _invoiceAmountBOB,
        uint256  _lockedForexRate,
        uint256  _escrowAmountPEN,
        uint256  _settlementAmountPEN,
        string memory _description,
        string memory _shipmentRef,
        address  _courtCase,
        uint8    _courtVerdict
    ) {
        return (
            status, exporter, importer, invoiceAmountBOB, lockedForexRate,
            escrowAmountPEN, settlementAmountPEN, description, shipmentRef,
            courtCase, courtVerdict
        );
    }
}
