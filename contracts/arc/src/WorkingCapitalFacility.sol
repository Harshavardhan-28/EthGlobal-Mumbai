// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {LenderNote} from "./LenderNote.sol";

/// @title WorkingCapitalFacility
/// @notice Receivables-backed USDC credit facility shared by a team of AI agents.
///         One GST-verified receivable opens one facility with a borrowing base set by
///         the engine's evidence tier and credit score (attested by the coordinator).
///         Lenders fund it (directly, or through an Aqua/SwapVM fill) and receive
///         non-transferable lender notes. The operator splits the funded principal
///         into agent budgets; agents spend through a reserve -> commit/release flow.
///         Customer repayment pays lenders principal plus fee before the operator.
/// @dev Hedera and Arc are separate environments. Facts observed on Hedera
///      (receivable verification, x402 settlement) reach this contract as short-lived
///      EIP-712 attestations signed by the coordinator. The coordinator is a trust
///      assumption of this prototype. Not audited.
contract WorkingCapitalFacility is EIP712, Ownable2Step, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------- errors
    error Unauthorized();
    error InvalidAmount();
    error InvalidSignature();
    error AttestationExpired();
    error AttestationReplayed();
    error DuplicateReceivable();
    error StaleReceivable();
    error UnknownFacility();
    error FacilityNotActive();
    error BorrowingBaseExceeded();
    error BudgetCeilingExceeded();
    error AgentBudgetExceeded();
    error GlobalCreditLimitExceeded();
    error TransactionLimitExceeded();
    error DailyLimitExceeded();
    error MerchantNotAllowed();
    error ReservationNotPending();
    error ReservationNotExpired();
    error DuplicateIntent();
    error AquaFillMismatch();
    error AquaNotConfigured();
    error TooManyLenders();
    error LenderNotApproved();
    error AddressBlocked();
    error InvalidStatus();
    error NotYetOverdue();

    // ---------------------------------------------------------------- types
    /// @dev New states are appended so existing integrations keep their numbers (Repaid = 3).
    enum Status {
        None,
        Open,
        Active,
        Repaid,
        Overdue,
        Defaulted,
        Disputed
    }

    enum ReservationState {
        None,
        Reserved,
        Committed,
        Released
    }

    struct Facility {
        address operator;
        address treasury;
        bytes32 receivableId;
        uint128 faceValue;
        uint128 borrowingBase;
        uint128 funded;
        uint128 totalAssigned;
        uint128 totalReserved;
        uint128 totalSpent;
        uint128 lenderDue;
        uint128 lenderPaid;
        uint128 customerPaid;
        uint128 operatorPaid;
        uint64 maturity;
        uint64 verificationExpiry;
        bool duesCrystallized;
        Status status;
    }

    struct Allocation {
        address lender;
        uint128 principal;
        uint128 notes;
        uint128 due;
        uint128 paid;
        uint64 fundedAt;
        uint32 minTermDays;
        uint16 aprBps;
    }

    struct Budget {
        uint128 allocation;
        uint128 spent;
        uint128 reserved;
        uint128 perTxLimit;
        uint128 dailyLimit;
        uint128 dayUsed;
        uint64 day;
    }

    struct Reservation {
        address agent;
        bytes32 merchant;
        uint128 amount;
        uint64 expiresAt;
        ReservationState state;
    }

    /// @notice Coordinator attestation that a receivable passed GST verification, screening and credit scoring.
    struct ReceivableAttestation {
        bytes32 facilityId;
        bytes32 receivableId;
        address operator;
        address treasury;
        uint128 faceValue;
        uint16 advanceRateBps;
        uint64 maturity;
        uint64 verificationExpiry;
        bytes32 hederaTxHash;
        uint64 hcsSequence;
        uint64 expiry;
    }

    /// @notice Coordinator attestation of one lender allocation chosen by the solver.
    struct FundingAttestation {
        bytes32 facilityId;
        address lender;
        uint128 principal;
        uint16 aprBps;
        uint32 minTermDays;
        bytes32 strategyHash;
        uint64 expiry;
    }

    /// @notice Coordinator attestation that a reserved spend settled through x402 on Hedera.
    struct SpendAttestation {
        bytes32 facilityId;
        bytes32 intentId;
        uint128 amount;
        bytes32 hederaTxHash;
        uint64 hcsSequence;
        uint64 expiry;
    }

    bytes32 public constant RECEIVABLE_TYPEHASH = keccak256(
        "ReceivableAttestation(bytes32 facilityId,bytes32 receivableId,address operator,address treasury,uint128 faceValue,uint16 advanceRateBps,uint64 maturity,uint64 verificationExpiry,bytes32 hederaTxHash,uint64 hcsSequence,uint64 expiry)"
    );
    bytes32 public constant FUNDING_TYPEHASH = keccak256(
        "FundingAttestation(bytes32 facilityId,address lender,uint128 principal,uint16 aprBps,uint32 minTermDays,bytes32 strategyHash,uint64 expiry)"
    );
    bytes32 public constant SPEND_TYPEHASH = keccak256(
        "SpendAttestation(bytes32 facilityId,bytes32 intentId,uint128 amount,bytes32 hederaTxHash,uint64 hcsSequence,uint64 expiry)"
    );

    uint256 public constant MAX_LENDERS = 8;
    /// @notice Unpaid this long after maturity -> anyone may mark the facility Overdue.
    uint256 public constant GRACE_PERIOD = 7 days;
    /// @notice Unpaid this long after maturity -> anyone may mark the facility Defaulted.
    uint256 public constant DEFAULT_AFTER = 30 days;
    uint256 private constant BPS = 10_000;
    uint256 private constant YEAR = 365 days;

    // ---------------------------------------------------------------- state
    IERC20 public immutable usdc;
    LenderNote public immutable note;
    address public coordinator;
    address public aquaRouter;
    address public aquaRegistry;

    mapping(bytes32 => Facility) private _facilities;
    mapping(bytes32 => Allocation[]) private _allocations;
    mapping(bytes32 => mapping(address => Budget)) private _budgets;
    mapping(bytes32 => mapping(address => mapping(bytes32 => bool))) public merchantAllowed;
    mapping(bytes32 => mapping(bytes32 => Reservation)) private _reservations;
    mapping(bytes32 => bool) public usedReceivables;
    mapping(bytes32 => bool) public usedAttestations;
    /// @notice KYC-approved lenders. Only these can fund.
    mapping(address => bool) public approvedLender;
    /// @notice Sanctions-screened addresses that may not open, fund, spend or repay.
    mapping(address => bool) public blocked;
    mapping(bytes32 => Status) private _statusBeforeDispute;

    // ---------------------------------------------------------------- events
    event CoordinatorUpdated(address indexed coordinator);
    event AquaRouterUpdated(address indexed router, address indexed registry);
    event FacilityOpened(
        bytes32 indexed facilityId,
        bytes32 indexed receivableId,
        address indexed operator,
        uint256 borrowingBase,
        bytes32 hederaTxHash,
        uint64 hcsSequence
    );
    event LenderFunded(
        bytes32 indexed facilityId,
        address indexed lender,
        uint256 principal,
        uint256 notes,
        uint16 aprBps,
        bytes32 strategyHash,
        bool viaAqua
    );
    event BudgetAssigned(
        bytes32 indexed facilityId, address indexed agent, uint256 allocation, uint256 perTxLimit, uint256 dailyLimit
    );
    event MerchantSet(bytes32 indexed facilityId, address indexed agent, bytes32 indexed merchant, bool allowed);
    event SpendReserved(
        bytes32 indexed facilityId,
        bytes32 indexed intentId,
        address indexed agent,
        bytes32 merchant,
        uint256 amount,
        uint64 expiresAt
    );
    event SpendCommitted(
        bytes32 indexed facilityId,
        bytes32 indexed intentId,
        address indexed agent,
        uint256 amount,
        bytes32 hederaTxHash,
        uint64 hcsSequence
    );
    event SpendReleased(bytes32 indexed facilityId, bytes32 indexed intentId, address indexed agent, uint256 amount);
    event RepaymentReceived(
        bytes32 indexed facilityId, address indexed payer, uint256 amount, uint256 toLenders, uint256 toOperator
    );
    event LenderRepaid(bytes32 indexed facilityId, address indexed lender, uint256 amount, bool settled);
    event FacilityClosed(bytes32 indexed facilityId, uint256 undrawnReturned);
    event LenderApprovalSet(address indexed lender, bool approved);
    event AddressBlockSet(address indexed account, bool isBlocked);
    event FacilityStatusChanged(bytes32 indexed facilityId, Status status);

    constructor(address usdc_, address coordinator_, address owner_)
        EIP712("WorkingCapitalFacility", "1")
        Ownable(owner_)
    {
        if (usdc_ == address(0) || coordinator_ == address(0)) {
            revert InvalidAmount();
        }
        usdc = IERC20(usdc_);
        coordinator = coordinator_;
        note = new LenderNote(address(this));
        emit CoordinatorUpdated(coordinator_);
    }

    // ---------------------------------------------------------------- admin
    function setCoordinator(address coordinator_) external onlyOwner {
        if (coordinator_ == address(0)) revert InvalidAmount();
        coordinator = coordinator_;
        emit CoordinatorUpdated(coordinator_);
    }

    /// @notice Set the official AquaSwapVMRouter and Aqua registry used for lender fills.
    ///         Zero addresses disable Aqua funding.
    function setAqua(address router, address registry) external onlyOwner {
        if (aquaRouter != address(0)) note.setTransferAgent(aquaRouter, false);
        if (aquaRegistry != address(0)) note.setTransferAgent(aquaRegistry, false);
        aquaRouter = router;
        aquaRegistry = registry;
        if (router != address(0)) note.setTransferAgent(router, true);
        if (registry != address(0)) note.setTransferAgent(registry, true);
        emit AquaRouterUpdated(router, registry);
    }

    // ---------------------------------------------------------------- compliance
    /// @notice Approve or remove a lender after KYC. The owner is the compliance admin.
    function setLenderApproval(address lender, bool approved) external onlyOwner {
        if (lender == address(0)) revert InvalidAmount();
        approvedLender[lender] = approved;
        emit LenderApprovalSet(lender, approved);
    }

    /// @notice Block or unblock an address after sanctions screening.
    function setBlocked(address account, bool isBlocked) external onlyOwner {
        if (account == address(0)) revert InvalidAmount();
        blocked[account] = isBlocked;
        emit AddressBlockSet(account, isBlocked);
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // ---------------------------------------------------------------- views
    function facility(bytes32 facilityId) external view returns (Facility memory) {
        return _facilities[facilityId];
    }

    function allocations(bytes32 facilityId) external view returns (Allocation[] memory) {
        return _allocations[facilityId];
    }

    function budget(bytes32 facilityId, address agent) external view returns (Budget memory) {
        return _budgets[facilityId][agent];
    }

    function reservation(bytes32 facilityId, bytes32 intentId) external view returns (Reservation memory) {
        return _reservations[facilityId][intentId];
    }

    /// @notice Credit still drawable by agents: funded principal minus spent and reserved.
    function availableCredit(bytes32 facilityId) public view returns (uint256) {
        Facility storage f = _facilities[facilityId];
        return uint256(f.funded) - f.totalSpent - f.totalReserved;
    }

    /// @notice Lender fee for a principal: principal x APR x max(elapsed, minimum term) / 365 days.
    function lenderFee(uint256 principal, uint16 aprBps, uint256 elapsed, uint32 minTermDays)
        public
        pure
        returns (uint256)
    {
        uint256 term = elapsed > uint256(minTermDays) * 1 days ? elapsed : uint256(minTermDays) * 1 days;
        return principal * aprBps * term / (BPS * YEAR);
    }

    function hashReceivable(ReceivableAttestation calldata a) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    RECEIVABLE_TYPEHASH,
                    a.facilityId,
                    a.receivableId,
                    a.operator,
                    a.treasury,
                    a.faceValue,
                    a.advanceRateBps,
                    a.maturity,
                    a.verificationExpiry,
                    a.hederaTxHash,
                    a.hcsSequence,
                    a.expiry
                )
            )
        );
    }

    function hashFunding(FundingAttestation calldata a) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    FUNDING_TYPEHASH,
                    a.facilityId,
                    a.lender,
                    a.principal,
                    a.aprBps,
                    a.minTermDays,
                    a.strategyHash,
                    a.expiry
                )
            )
        );
    }

    function hashSpend(SpendAttestation calldata a) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(SPEND_TYPEHASH, a.facilityId, a.intentId, a.amount, a.hederaTxHash, a.hcsSequence, a.expiry)
            )
        );
    }

    // ---------------------------------------------------------------- verify
    /// @notice Open a facility from a coordinator-attested, GST-verified receivable.
    function openFacility(ReceivableAttestation calldata a, bytes calldata signature) external whenNotPaused {
        _consume(hashReceivable(a), a.expiry, signature);
        if (a.receivableId == bytes32(0) || a.operator == address(0) || a.treasury == address(0)) {
            revert InvalidAmount();
        }
        if (a.faceValue == 0 || a.advanceRateBps == 0 || a.advanceRateBps > BPS) revert InvalidAmount();
        if (blocked[a.operator] || blocked[a.treasury]) revert AddressBlocked();
        if (a.verificationExpiry <= block.timestamp) revert StaleReceivable();
        if (usedReceivables[a.receivableId]) revert DuplicateReceivable();
        Facility storage f = _facilities[a.facilityId];
        if (f.status != Status.None) revert DuplicateReceivable();

        usedReceivables[a.receivableId] = true;
        f.operator = a.operator;
        f.treasury = a.treasury;
        f.receivableId = a.receivableId;
        f.faceValue = a.faceValue;
        f.borrowingBase = uint128(uint256(a.faceValue) * a.advanceRateBps / BPS);
        f.maturity = a.maturity;
        f.verificationExpiry = a.verificationExpiry;
        f.status = Status.Open;
        emit FacilityOpened(a.facilityId, a.receivableId, a.operator, f.borrowingBase, a.hederaTxHash, a.hcsSequence);
    }

    // ---------------------------------------------------------------- fund
    /// @notice Lender funds an attested allocation directly with USDC and receives notes 1:1.
    function fundDirect(FundingAttestation calldata a, bytes calldata signature) external nonReentrant whenNotPaused {
        if (msg.sender != a.lender) revert Unauthorized();
        _checkLender(a.lender);
        _consume(hashFunding(a), a.expiry, signature);
        Facility storage f = _fundable(a.facilityId, a.principal);
        usdc.safeTransferFrom(a.lender, address(this), a.principal);
        note.mint(a.lender, a.principal);
        _recordAllocation(f, a, a.principal, false);
    }

    /// @notice Fill an attested lender allocation through the official Aqua SwapVM router.
    /// @dev The facility is the taker: it mints up to `maxNotes` lender notes, lets the
    ///      router pull them, and receives exactly `principal` USDC from the lender's
    ///      shipped Aqua strategy. The lender's note balance must rise by exactly the
    ///      notes spent, proving the fill came from that lender's strategy.
    function fundViaAqua(
        FundingAttestation calldata a,
        bytes calldata signature,
        bytes calldata swapCalldata,
        uint128 maxNotes
    ) external nonReentrant whenNotPaused {
        if (msg.sender != coordinator) revert Unauthorized();
        address router = aquaRouter;
        if (router == address(0)) revert AquaNotConfigured();
        if (maxNotes < a.principal) revert InvalidAmount();
        _checkLender(a.lender);
        _consume(hashFunding(a), a.expiry, signature);
        Facility storage f = _fundable(a.facilityId, a.principal);

        uint256 usdcBefore = usdc.balanceOf(address(this));
        uint256 lenderNotesBefore = note.balanceOf(a.lender);
        note.mint(address(this), maxNotes);
        IERC20(address(note)).forceApprove(router, maxNotes);
        if (aquaRegistry != address(0)) IERC20(address(note)).forceApprove(aquaRegistry, maxNotes);
        (bool ok, bytes memory reason) = router.call(swapCalldata);
        if (!ok) {
            assembly ("memory-safe") {
                revert(add(reason, 32), mload(reason))
            }
        }
        IERC20(address(note)).forceApprove(router, 0);
        if (aquaRegistry != address(0)) IERC20(address(note)).forceApprove(aquaRegistry, 0);

        uint256 leftover = note.balanceOf(address(this));
        uint256 notesSpent = uint256(maxNotes) - leftover;
        if (leftover > 0) note.redeem(address(this), leftover);
        if (usdc.balanceOf(address(this)) - usdcBefore != a.principal) revert AquaFillMismatch();
        if (note.balanceOf(a.lender) - lenderNotesBefore != notesSpent) revert AquaFillMismatch();
        _recordAllocation(f, a, uint128(notesSpent), true);
    }

    // ---------------------------------------------------------------- budgets
    function assignBudget(bytes32 facilityId, address agent, uint128 allocation, uint128 perTxLimit, uint128 dailyLimit)
        external
    {
        Facility storage f = _facilities[facilityId];
        if (msg.sender != f.operator) revert Unauthorized();
        if (f.status != Status.Active) revert FacilityNotActive();
        if (agent == address(0) || agent == f.operator) revert InvalidAmount();
        if (blocked[agent]) revert AddressBlocked();
        Budget storage b = _budgets[facilityId][agent];
        if (allocation < uint256(b.spent) + b.reserved) revert BudgetCeilingExceeded();
        uint256 assigned = uint256(f.totalAssigned) - b.allocation + allocation;
        if (assigned > f.funded) revert BudgetCeilingExceeded();
        f.totalAssigned = uint128(assigned);
        b.allocation = allocation;
        b.perTxLimit = perTxLimit;
        b.dailyLimit = dailyLimit;
        emit BudgetAssigned(facilityId, agent, allocation, perTxLimit, dailyLimit);
    }

    function setMerchant(bytes32 facilityId, address agent, bytes32 merchant, bool allowed) external {
        if (msg.sender != _facilities[facilityId].operator) revert Unauthorized();
        merchantAllowed[facilityId][agent][merchant] = allowed;
        emit MerchantSet(facilityId, agent, merchant, allowed);
    }

    // ---------------------------------------------------------------- spend
    /// @notice Reserve credit for an agent payment before it is paid on Hedera.
    /// @dev Callable by the agent or by the coordinator on the agent's behalf.
    function reserveSpend(
        bytes32 facilityId,
        bytes32 intentId,
        address agent,
        bytes32 merchant,
        uint128 amount,
        uint64 ttl
    ) external whenNotPaused {
        if (msg.sender != agent && msg.sender != coordinator) revert Unauthorized();
        if (blocked[agent]) revert AddressBlocked();
        Facility storage f = _facilities[facilityId];
        if (f.status != Status.Active) revert FacilityNotActive();
        if (f.verificationExpiry <= block.timestamp) revert StaleReceivable();
        if (amount == 0 || intentId == bytes32(0) || ttl == 0) revert InvalidAmount();
        if (_reservations[facilityId][intentId].state != ReservationState.None) revert DuplicateIntent();
        // Capacity first (facility ceiling, then agent allocation), then policy limits.
        // The operator acts as the lead agent: it is bound by the facility ceiling, not a sub-budget.
        if (uint256(f.totalSpent) + f.totalReserved + amount > f.funded) revert GlobalCreditLimitExceeded();
        Budget storage b = _budgets[facilityId][agent];
        if (agent != f.operator && uint256(b.spent) + b.reserved + amount > b.allocation) revert AgentBudgetExceeded();
        if (b.perTxLimit != 0 && amount > b.perTxLimit) revert TransactionLimitExceeded();
        uint64 today = uint64(block.timestamp / 1 days);
        if (b.day != today) {
            b.day = today;
            b.dayUsed = 0;
        }
        if (b.dailyLimit != 0 && uint256(b.dayUsed) + amount > b.dailyLimit) revert DailyLimitExceeded();
        if (!merchantAllowed[facilityId][agent][merchant]) revert MerchantNotAllowed();

        b.dayUsed += amount;
        b.reserved += amount;
        f.totalReserved += amount;
        uint64 expiresAt = uint64(block.timestamp) + ttl;
        _reservations[facilityId][intentId] = Reservation(agent, merchant, amount, expiresAt, ReservationState.Reserved);
        emit SpendReserved(facilityId, intentId, agent, merchant, amount, expiresAt);
    }

    /// @notice Commit a reserved spend once the coordinator attests the finalized Hedera settlement.
    ///         The drawn USDC moves to the operator treasury that reimburses the agent's Hedera wallet.
    function commitSpend(SpendAttestation calldata a, bytes calldata signature) external nonReentrant {
        _consume(hashSpend(a), a.expiry, signature);
        Reservation storage r = _reservations[a.facilityId][a.intentId];
        if (r.state != ReservationState.Reserved) revert ReservationNotPending();
        if (a.amount != r.amount) revert InvalidAmount();
        Facility storage f = _facilities[a.facilityId];
        Budget storage b = _budgets[a.facilityId][r.agent];
        r.state = ReservationState.Committed;
        b.reserved -= a.amount;
        b.spent += a.amount;
        f.totalReserved -= a.amount;
        f.totalSpent += a.amount;
        usdc.safeTransfer(f.treasury, a.amount);
        emit SpendCommitted(a.facilityId, a.intentId, r.agent, a.amount, a.hederaTxHash, a.hcsSequence);
    }

    /// @notice Release a reservation after a confirmed failure (coordinator) or timeout (anyone).
    function releaseSpend(bytes32 facilityId, bytes32 intentId) external {
        Reservation storage r = _reservations[facilityId][intentId];
        if (r.state != ReservationState.Reserved) revert ReservationNotPending();
        if (msg.sender != coordinator && block.timestamp < r.expiresAt) revert ReservationNotExpired();
        Budget storage b = _budgets[facilityId][r.agent];
        r.state = ReservationState.Released;
        b.reserved -= r.amount;
        if (b.day == uint64(block.timestamp / 1 days)) b.dayUsed = b.dayUsed > r.amount ? b.dayUsed - r.amount : 0;
        _facilities[facilityId].totalReserved -= r.amount;
        emit SpendReleased(facilityId, intentId, r.agent, r.amount);
    }

    // ---------------------------------------------------------------- repay
    /// @notice Customer repayment into the fixed router. Lenders are paid principal plus fee,
    ///         pro rata, before any amount reaches the operator. Supports partial payments.
    ///         Works while Active, Overdue, Defaulted or Disputed: lenders can always be paid.
    function repay(bytes32 facilityId, uint128 amount) external nonReentrant {
        Facility storage f = _facilities[facilityId];
        if (!_repayable(f.status)) revert FacilityNotActive();
        if (amount == 0) revert InvalidAmount();
        if (blocked[msg.sender]) revert AddressBlocked();
        usdc.safeTransferFrom(msg.sender, address(this), amount);
        if (!f.duesCrystallized) _crystallize(facilityId, f);

        uint256 owed = uint256(f.lenderDue) - f.lenderPaid;
        uint256 toLenders = amount < owed ? amount : owed;
        if (toLenders > 0) _payLenders(facilityId, f, toLenders, owed);
        uint256 toOperator = uint256(amount) - toLenders;
        f.customerPaid += amount;
        if (toOperator > 0) {
            f.operatorPaid += uint128(toOperator);
            usdc.safeTransfer(f.operator, toOperator);
        }
        emit RepaymentReceived(facilityId, msg.sender, amount, toLenders, toOperator);

        if (f.lenderPaid == f.lenderDue && f.totalReserved == 0) {
            f.status = Status.Repaid;
            emit FacilityStatusChanged(facilityId, Status.Repaid);
            uint256 undrawn = uint256(f.funded) - f.totalSpent;
            if (undrawn > 0) usdc.safeTransfer(f.operator, undrawn);
            emit FacilityClosed(facilityId, undrawn);
        }
    }

    // ---------------------------------------------------------------- overdue, default, dispute
    /// @notice Anyone can mark an unpaid facility Overdue once the grace period has passed.
    ///         Agent spending stops; repayment keeps working.
    function markOverdue(bytes32 facilityId) external {
        Facility storage f = _facilities[facilityId];
        if (f.status != Status.Active) revert InvalidStatus();
        if (block.timestamp < uint256(f.maturity) + GRACE_PERIOD) revert NotYetOverdue();
        _setStatus(facilityId, f, Status.Overdue);
    }

    /// @notice Anyone can mark an unpaid facility Defaulted 30 days after maturity.
    function markDefaulted(bytes32 facilityId) external {
        Facility storage f = _facilities[facilityId];
        if (f.status != Status.Active && f.status != Status.Overdue) revert InvalidStatus();
        if (block.timestamp < uint256(f.maturity) + DEFAULT_AFTER) revert NotYetOverdue();
        _setStatus(facilityId, f, Status.Defaulted);
    }

    /// @notice A disputed invoice freezes new spending (coordinator or owner).
    function flagDispute(bytes32 facilityId) external {
        if (msg.sender != coordinator && msg.sender != owner()) revert Unauthorized();
        Facility storage f = _facilities[facilityId];
        if (f.status != Status.Active && f.status != Status.Overdue) revert InvalidStatus();
        _statusBeforeDispute[facilityId] = f.status;
        _setStatus(facilityId, f, Status.Disputed);
    }

    /// @notice Upheld: the invoice is not owed, so the seller is in default (recourse).
    ///         Not upheld: the facility returns to the state it was in.
    function resolveDispute(bytes32 facilityId, bool upheld) external {
        if (msg.sender != coordinator && msg.sender != owner()) revert Unauthorized();
        Facility storage f = _facilities[facilityId];
        if (f.status != Status.Disputed) revert InvalidStatus();
        _setStatus(facilityId, f, upheld ? Status.Defaulted : _statusBeforeDispute[facilityId]);
    }

    /// @notice Total still owed to lenders (fees estimated at the current time before crystallization).
    function lenderOutstanding(bytes32 facilityId) external view returns (uint256 total) {
        Facility storage f = _facilities[facilityId];
        if (f.duesCrystallized) return uint256(f.lenderDue) - f.lenderPaid;
        Allocation[] storage list = _allocations[facilityId];
        for (uint256 i; i < list.length; ++i) {
            Allocation storage al = list[i];
            total += al.principal + lenderFee(al.principal, al.aprBps, block.timestamp - al.fundedAt, al.minTermDays);
        }
    }

    // ---------------------------------------------------------------- internal
    function _repayable(Status s) private pure returns (bool) {
        return s == Status.Active || s == Status.Overdue || s == Status.Defaulted || s == Status.Disputed;
    }

    function _setStatus(bytes32 facilityId, Facility storage f, Status s) private {
        f.status = s;
        emit FacilityStatusChanged(facilityId, s);
    }

    function _checkLender(address lender) private view {
        if (blocked[lender]) revert AddressBlocked();
        if (!approvedLender[lender]) revert LenderNotApproved();
    }

    function _consume(bytes32 digest, uint64 expiry, bytes calldata signature) private {
        if (expiry < block.timestamp) revert AttestationExpired();
        if (usedAttestations[digest]) revert AttestationReplayed();
        (address signer, ECDSA.RecoverError err,) = ECDSA.tryRecover(digest, signature);
        if (err != ECDSA.RecoverError.NoError || signer != coordinator) revert InvalidSignature();
        usedAttestations[digest] = true;
    }

    function _fundable(bytes32 facilityId, uint128 principal) private view returns (Facility storage f) {
        f = _facilities[facilityId];
        if (f.status == Status.None) revert UnknownFacility();
        if (f.status != Status.Open && f.status != Status.Active) revert FacilityNotActive();
        if (f.verificationExpiry <= block.timestamp) revert StaleReceivable();
        if (principal == 0) revert InvalidAmount();
        if (uint256(f.funded) + principal > f.borrowingBase) revert BorrowingBaseExceeded();
        if (_allocations[facilityId].length >= MAX_LENDERS) revert TooManyLenders();
    }

    function _recordAllocation(Facility storage f, FundingAttestation calldata a, uint128 notes, bool viaAqua) private {
        _allocations[a.facilityId].push(
            Allocation(a.lender, a.principal, notes, 0, 0, uint64(block.timestamp), a.minTermDays, a.aprBps)
        );
        f.funded += a.principal;
        f.status = Status.Active;
        emit LenderFunded(a.facilityId, a.lender, a.principal, notes, a.aprBps, a.strategyHash, viaAqua);
    }

    function _crystallize(bytes32 facilityId, Facility storage f) private {
        Allocation[] storage list = _allocations[facilityId];
        uint256 total;
        for (uint256 i; i < list.length; ++i) {
            Allocation storage al = list[i];
            uint256 due = uint256(al.principal)
                + lenderFee(al.principal, al.aprBps, block.timestamp - al.fundedAt, al.minTermDays);
            al.due = uint128(due);
            total += due;
        }
        f.lenderDue = uint128(total);
        f.duesCrystallized = true;
    }

    function _payLenders(bytes32 facilityId, Facility storage f, uint256 amount, uint256 owed) private {
        Allocation[] storage list = _allocations[facilityId];
        uint256 n = list.length;
        uint256[] memory shares = new uint256[](n);
        uint256 distributed;
        for (uint256 i; i < n; ++i) {
            uint256 remaining = uint256(list[i].due) - list[i].paid;
            uint256 share = amount == owed ? remaining : amount * remaining / owed;
            shares[i] = share;
            distributed += share;
        }
        // Pro-rata flooring leaves at most (n - 1) base units; give them to lenders with capacity, in order.
        for (uint256 i; i < n && distributed < amount; ++i) {
            uint256 capacity = uint256(list[i].due) - list[i].paid - shares[i];
            uint256 extra = amount - distributed < capacity ? amount - distributed : capacity;
            shares[i] += extra;
            distributed += extra;
        }
        for (uint256 i; i < n; ++i) {
            if (shares[i] == 0) continue;
            Allocation storage al = list[i];
            al.paid += uint128(shares[i]);
            bool settled = al.paid == al.due;
            usdc.safeTransfer(al.lender, shares[i]);
            if (settled) {
                uint256 held = note.balanceOf(al.lender);
                note.redeem(al.lender, held < al.notes ? held : al.notes);
            }
            emit LenderRepaid(facilityId, al.lender, shares[i], settled);
        }
        f.lenderPaid += uint128(distributed);
    }
}
