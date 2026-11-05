// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {FacilityBase} from "./Base.t.sol";
import {WorkingCapitalFacility} from "../src/WorkingCapitalFacility.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @notice Compliance gates (lender allowlist, blocked addresses) and the
///         overdue / default / dispute states, mirroring the Python engine.
contract ComplianceTest is FacilityBase {
    address internal eve = makeAddr("eve-unapproved-lender");

    // ------------------------------------------------------------ allowlist and screening
    function test_RevertWhen_LenderNotApproved() public {
        openFacility();
        usdc.mint(eve, 1_000 * USD);
        vm.prank(eve);
        usdc.approve(address(facility), type(uint256).max);
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(eve, 1_000 * USD, 700);
        bytes memory sig = signFunding(a);
        vm.prank(eve);
        vm.expectRevert(WorkingCapitalFacility.LenderNotApproved.selector);
        facility.fundDirect(a, sig);
    }

    function test_RevertWhen_LenderIsBlocked() public {
        openFacility();
        facility.setBlocked(alice, true);
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(alice, 1_000 * USD, 700);
        bytes memory sig = signFunding(a);
        vm.prank(alice);
        vm.expectRevert(WorkingCapitalFacility.AddressBlocked.selector);
        facility.fundDirect(a, sig);
    }

    function test_RevertWhen_OperatorIsBlockedAtOpen() public {
        facility.setBlocked(operator, true);
        WorkingCapitalFacility.ReceivableAttestation memory a = receivableAttestation();
        bytes memory sig = signReceivable(a);
        vm.expectRevert(WorkingCapitalFacility.AddressBlocked.selector);
        facility.openFacility(a, sig);
    }

    function test_RevertWhen_AgentIsBlocked() public {
        openAndFund();
        assignDemoBudgets();
        facility.setBlocked(research, true);
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.AddressBlocked.selector);
        facility.reserveSpend(FACILITY, keccak256("i1"), research, MERCHANT, USD / 4, 5 minutes);
    }

    function test_RevertWhen_BlockedPayerRepays() public {
        openAndFund();
        facility.setBlocked(customer, true);
        vm.prank(customer);
        vm.expectRevert(WorkingCapitalFacility.AddressBlocked.selector);
        facility.repay(FACILITY, 1_000 * USD);
    }

    function test_OnlyOwnerSetsCompliance() public {
        vm.startPrank(operator);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, operator));
        facility.setLenderApproval(eve, true);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, operator));
        facility.setBlocked(alice, true);
        vm.stopPrank();
    }

    // ------------------------------------------------------------ overdue and default
    function test_MarkOverdueOnlyAfterGraceAndItFreezesSpending() public {
        openAndFund();
        assignDemoBudgets();
        uint256 maturity = block.timestamp + 30 days;
        vm.warp(maturity + 7 days - 1);
        vm.expectRevert(WorkingCapitalFacility.NotYetOverdue.selector);
        facility.markOverdue(FACILITY);
        vm.warp(maturity + 7 days);
        vm.prank(makeAddr("anyone"));
        facility.markOverdue(FACILITY);
        assertEq(uint8(facility.facility(FACILITY).status), uint8(WorkingCapitalFacility.Status.Overdue));
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.FacilityNotActive.selector);
        facility.reserveSpend(FACILITY, keccak256("late"), research, MERCHANT, USD / 4, 5 minutes);
    }

    function test_DefaultedFacilityStillPaysLendersBeforeOperator() public {
        openAndFund();
        uint256 maturity = block.timestamp + 30 days;
        vm.warp(maturity + 30 days);
        facility.markDefaulted(FACILITY);
        assertEq(uint8(facility.facility(FACILITY).status), uint8(WorkingCapitalFacility.Status.Defaulted));

        uint256 operatorBefore = usdc.balanceOf(operator);
        vm.prank(customer);
        facility.repay(FACILITY, 3_000 * USD); // partial recovery
        assertEq(usdc.balanceOf(operator), operatorBefore, "operator gets nothing while lenders are owed");
        assertEq(uint8(facility.facility(FACILITY).status), uint8(WorkingCapitalFacility.Status.Defaulted));

        vm.prank(customer);
        facility.repay(FACILITY, 7_000 * USD);
        WorkingCapitalFacility.Facility memory f = facility.facility(FACILITY);
        assertEq(uint8(f.status), uint8(WorkingCapitalFacility.Status.Repaid));
        assertEq(f.lenderPaid, f.lenderDue);
        // 60 days of interest instead of the 30-day minimum: $4,000 at 7% + $2,000 at 8.5%.
        assertEq(
            f.lenderDue,
            6_000 * USD + facility.lenderFee(4_000 * USD, 700, 60 days, 30)
                + facility.lenderFee(2_000 * USD, 850, 60 days, 30)
        );
    }

    function test_RevertWhen_DefaultMarkedEarly() public {
        openAndFund();
        vm.warp(block.timestamp + 30 days + 29 days);
        vm.expectRevert(WorkingCapitalFacility.NotYetOverdue.selector);
        facility.markDefaulted(FACILITY);
    }

    function test_RevertWhen_FundingAnOverdueFacility() public {
        openFacility();
        fund(alice, 4_000 * USD, 700);
        vm.warp(block.timestamp + 37 days);
        facility.markOverdue(FACILITY);
        WorkingCapitalFacility.FundingAttestation memory a = fundingAttestation(bob, 1_000 * USD, 850);
        a.expiry = uint64(block.timestamp + 10 minutes);
        bytes memory sig = signFunding(a);
        vm.prank(bob);
        vm.expectRevert(WorkingCapitalFacility.FacilityNotActive.selector);
        facility.fundDirect(a, sig);
    }

    // ------------------------------------------------------------ disputes
    function test_DisputeFreezesSpendingAndClearsBackToActive() public {
        openAndFund();
        assignDemoBudgets();
        vm.prank(coordinator);
        facility.flagDispute(FACILITY);
        vm.prank(coordinator);
        vm.expectRevert(WorkingCapitalFacility.FacilityNotActive.selector);
        facility.reserveSpend(FACILITY, keccak256("d1"), research, MERCHANT, USD / 4, 5 minutes);

        vm.prank(coordinator);
        facility.resolveDispute(FACILITY, false);
        assertEq(uint8(facility.facility(FACILITY).status), uint8(WorkingCapitalFacility.Status.Active));
        reserve(keccak256("d2"), research, USD / 4);
    }

    function test_UpheldDisputeIsADefault() public {
        openAndFund();
        facility.flagDispute(FACILITY); // the owner may also flag
        facility.resolveDispute(FACILITY, true);
        assertEq(uint8(facility.facility(FACILITY).status), uint8(WorkingCapitalFacility.Status.Defaulted));
    }

    function test_RevertWhen_StrangerFlagsDispute() public {
        openAndFund();
        vm.prank(makeAddr("stranger"));
        vm.expectRevert(WorkingCapitalFacility.Unauthorized.selector);
        facility.flagDispute(FACILITY);
    }
}
