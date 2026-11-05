// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @notice Test-only 6-decimal USDC stand-in for the local Arc fork.
/// @dev Arc's native USDC ERC-20 interface relies on an Arc precompile that
///      Anvil cannot emulate, so the fork demo uses this clearly labelled token.
contract TestUSDC is ERC20, Ownable {
    constructor(address initialOwner) ERC20("Test USDC (local Arc fork)", "tUSDC") Ownable(initialOwner) {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }
}
