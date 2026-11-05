// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Receivable-backed lender note. One note unit = one USDC base unit of principal.
/// @dev Only the issuing facility can mint or redeem. Notes are delivered to lenders
///      when they fund a facility (directly or through an Aqua/SwapVM fill) and are
///      burned by the facility when the repayment waterfall pays that lender.
contract LenderNote is ERC20 {
    error OnlyFacility();
    error NonTransferable();

    address public immutable facility;
    /// @notice Settlement contracts (the official Aqua registry and SwapVM router) that may relay notes.
    mapping(address => bool) public transferAgent;

    event TransferAgentSet(address indexed agent, bool allowed);

    constructor(address facility_) ERC20("Receivable Credit Note", "RCN") {
        facility = facility_;
    }

    modifier onlyFacility() {
        if (msg.sender != facility) revert OnlyFacility();
        _;
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external onlyFacility {
        _mint(to, amount);
    }

    function setTransferAgent(address agent, bool allowed) external onlyFacility {
        transferAgent[agent] = allowed;
        emit TransferAgentSet(agent, allowed);
    }

    function redeem(address from, uint256 amount) external onlyFacility {
        _burn(from, amount);
    }

    /// @dev Notes only move from the facility, or through an approved settlement contract
    ///      relaying an Aqua fill, to the lender. Lenders cannot pass notes to third parties,
    ///      so repayment always reaches the address that funded the allocation.
    function _update(address from, address to, uint256 value) internal override {
        bool mintOrBurn = from == address(0) || to == address(0);
        bool fromIssuer = from == facility || transferAgent[from];
        bool toIssuer = to == facility || transferAgent[to];
        if (!mintOrBurn && !fromIssuer && !toIssuer) revert NonTransferable();
        super._update(from, to, value);
    }
}
