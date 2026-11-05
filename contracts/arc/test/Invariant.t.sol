// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {FacilityBase} from "./Base.t.sol";
import {WorkingCapitalFacility} from "../src/WorkingCapitalFacility.sol";
import {TestUSDC} from "../src/mocks/TestUSDC.sol";
import {Test} from "forge-std/Test.sol";

interface ISpendSigner {
    function signSpendPublic(WorkingCapitalFacility.SpendAttestation memory a) external view returns (bytes memory);
}

/// @notice Drives random reserve / commit / release / repay sequences against one facility.
contract FacilityHandler is Test {
    WorkingCapitalFacility internal facility;
    TestUSDC internal usdc;
    ISpendSigner internal base;
    bytes32 internal immutable facilityId;
    bytes32 internal immutable merchant;
    address internal immutable coordinator;
    address[] internal agents;
    bytes32[] public intents;
    uint256 public nonce;

    constructor(
        ISpendSigner base_,
        WorkingCapitalFacility facility_,
        TestUSDC usdc_,
        bytes32 facilityId_,
        bytes32 merchant_,
        address coordinator_,
        address[] memory agents_
    ) {
        base = base_;
        facility = facility_;
        usdc = usdc_;
        facilityId = facilityId_;
        merchant = merchant_;
        coordinator = coordinator_;
        agents = agents_;
    }

    function reserve(uint256 agentSeed, uint128 amount) external {
        address agent = agents[agentSeed % agents.length];
        amount = uint128(bound(amount, 1, 3_500e6));
        bytes32 intent = keccak256(abi.encode("intent", nonce++));
        vm.prank(coordinator);
        try facility.reserveSpend(facilityId, intent, agent, merchant, amount, 1 hours) {
            intents.push(intent);
        } catch {}
    }

    function commit(uint256 seed) external {
        if (intents.length == 0) return;
        bytes32 intent = intents[seed % intents.length];
        WorkingCapitalFacility.Reservation memory r = facility.reservation(facilityId, intent);
        if (r.state != WorkingCapitalFacility.ReservationState.Reserved) return;
        WorkingCapitalFacility.SpendAttestation memory a = WorkingCapitalFacility.SpendAttestation(
            facilityId, intent, r.amount, keccak256(abi.encode(intent)), 1, uint64(block.timestamp + 1 hours)
        );
        bytes memory sig = base.signSpendPublic(a);
        try facility.commitSpend(a, sig) {} catch {}
    }

    function release(uint256 seed, uint32 wait) external {
        if (intents.length == 0) return;
        vm.warp(block.timestamp + bound(wait, 0, 2 hours));
        try facility.releaseSpend(facilityId, intents[seed % intents.length]) {} catch {}
    }

    function repay(uint128 amount) external {
        amount = uint128(bound(amount, 1, 4_000e6));
        usdc.mint(address(this), amount);
        usdc.approve(address(facility), amount);
        try facility.repay(facilityId, amount) {} catch {}
    }
}

contract FacilityInvariantTest is FacilityBase {
    FacilityHandler internal handler;

    function setUp() public override {
        super.setUp();
        openAndFund();
        assignDemoBudgets();
        vm.prank(operator);
        facility.setMerchant(FACILITY, dataAgent, MERCHANT, true);
        address[] memory agents = new address[](3);
        agents[0] = research;
        agents[1] = compute;
        agents[2] = dataAgent;
        handler =
            new FacilityHandler(ISpendSigner(address(this)), facility, usdc, FACILITY, MERCHANT, coordinator, agents);
        usdc.transferOwnership(address(handler));
        targetContract(address(handler));
    }

    function signSpendPublic(WorkingCapitalFacility.SpendAttestation memory a) external view returns (bytes memory) {
        return signSpend(a);
    }

    /// Debt never exceeds the borrowing base; spend never exceeds funded principal.
    function invariant_CreditCeiling() public view {
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertLe(f.funded, f.borrowingBase);
        assertLe(uint256(f.totalSpent) + f.totalReserved, f.funded);
    }

    /// No agent ever has more spent plus reserved than its allocation.
    function invariant_AgentBudgets() public view {
        address[3] memory agents = [research, compute, dataAgent];
        for (uint256 i; i < 3; ++i) {
            WorkingCapitalFacility.Budget memory b = facility.budget(FACILITY, agents[i]);
            assertLe(uint256(b.spent) + b.reserved, b.allocation);
        }
    }

    /// The operator is never paid while lenders are owed.
    function invariant_LendersBeforeOperator() public view {
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        if (f.operatorPaid > 0) assertEq(f.lenderPaid, f.lenderDue);
        assertLe(f.lenderPaid, f.lenderDue);
    }

    /// The facility always holds exactly the undrawn principal while active.
    function invariant_FacilitySolvency() public view {
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        uint256 expected = f.status == WorkingCapitalFacility.Status.Repaid ? 0 : uint256(f.funded) - f.totalSpent;
        assertEq(usdc.balanceOf(address(facility)), expected);
    }
}
