"""Simulation module for sandwich trading opportunities."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from .ray_log_decoder import POOL_CONFIGS
from .transaction import PoolDetails

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Results of a sandwich trade simulation."""

    front_run_profit: int
    back_run_profit: int
    gas_cost: int
    pool_fees: int
    net_profit: int
    is_profitable: bool


def calculate_price_impact(amount_in: int, amount_out: int, pool_type: str) -> Decimal:
    """Calculate price impact of a swap.

    Args:
        amount_in: Amount of tokens being swapped in
        amount_out: Amount of tokens received
        pool_type: Type of pool (e.g., "SOL/USDC")

    Returns:
        Decimal: Price impact as a decimal (e.g., 0.01 for 1%)
    """
    pool_config = POOL_CONFIGS.get(pool_type)
    if not pool_config:
        return Decimal("0")

    # Calculate price impact based on amounts and decimals
    decimals_in = pool_config["token_a_decimals"]
    decimals_out = pool_config["token_b_decimals"]

    amount_in_decimal = Decimal(amount_in) / Decimal(10**decimals_in)
    amount_out_decimal = Decimal(amount_out) / Decimal(10**decimals_out)

    # Price impact = (amount_out_expected - amount_out_actual) / amount_out_expected
    price_impact = (amount_out_decimal - amount_in_decimal) / amount_in_decimal
    return price_impact


def estimate_gas_cost(pool_type: str = None, market_conditions: str = "normal") -> int:
    """Estimate gas cost for sandwich transactions based on market conditions.

    Args:
        pool_type: Type of pool for specific adjustments
        market_conditions: Current market conditions ("normal", "congested", "high")

    Returns:
        int: Estimated gas cost in lamports
    """
    # Base compute units for different transaction types
    COMPUTE_UNITS = {
        "front_run": 200_000,  # More complex due to timing requirements
        "back_run": 150_000,   # Slightly simpler execution
    }
    
    # Fixed gas cost of 5000 lamports per transaction
    BASE_GAS_COST = 5000  # lamports per transaction
    
    # Adjust gas cost based on market conditions
    MARKET_MULTIPLIERS = {
        "normal": 1.0,      # Standard gas cost
        "congested": 1.5,   # 50% increase during congestion
        "high": 2.0,        # Double gas cost during high activity
    }
    
    # Get current market multiplier
    multiplier = MARKET_MULTIPLIERS.get(market_conditions, MARKET_MULTIPLIERS["normal"])
    
    # Calculate total gas cost for both transactions (front-run and back-run)
    total_gas_cost = BASE_GAS_COST * 2  # Two transactions
    
    # Apply market condition multiplier and add 20% safety buffer
    return int(total_gas_cost * multiplier * 1.2)


def simulate_sandwich(
    decoded_log: Dict,
    pool_details: PoolDetails,
    min_profit_threshold: int = 10_000_000,  # 0.01 SOL
    dry_run: bool = False,
) -> Optional[SimulationResult]:
    """Simulate a sandwich trade opportunity.

    Args:
        decoded_log: Decoded ray_log data
        pool_details: Pool configuration details
        min_profit_threshold: Minimum profit required (in lamports)
        dry_run: Whether to run in test mode

    Returns:
        SimulationResult if profitable, None otherwise
    """
    try:
        if not decoded_log or "amount_in" not in decoded_log:
            return None

        pool_type = decoded_log.get("pool_type")
        if not pool_type or pool_type not in POOL_CONFIGS:
            logger.debug(f"Unsupported pool type: {pool_type}")
            return None

        pool_config = POOL_CONFIGS[pool_type]
        amount_in = decoded_log["amount_in"]
        amount_out = decoded_log["amount_out"]

        # Skip small transactions
        if amount_in < pool_config["min_amount_threshold"]:
            logger.debug(
                f"Transaction too small: {amount_in} < {pool_config['min_amount_threshold']}"
            )
            return None

        # Calculate price impact
        price_impact = calculate_price_impact(amount_in, amount_out, pool_type)
        if price_impact < pool_config["min_price_impact"]:
            logger.debug(
                f"Price impact too low: {price_impact} < {pool_config['min_price_impact']}"
            )
            return None

        # Estimate trade sizes and profits
        # Front-run: Buy tokens before large trade (profit from price increase)
        front_run_size = amount_in // 4  # 25% of detected trade
        # If price impact is negative (price decreases), front-run profit is negative
        front_run_profit = int(front_run_size * price_impact * -1)  # Inverse price impact for front-run

        # Back-run: Sell tokens after large trade (profit from price decrease)
        # If price impact is negative (price decreases), back-run profit is positive
        back_run_profit = int(front_run_size * price_impact)

        # Calculate pool fees (0.25% per trade)
        POOL_FEE_RATE = Decimal("0.0025")  # 0.25% fee per trade
        front_run_fees = int(front_run_size * POOL_FEE_RATE)  # Front-run fees
        back_run_fees = int(front_run_size * POOL_FEE_RATE * (1 + price_impact))  # Back-run fees with price impact
        pool_fees = front_run_fees + back_run_fees  # Total fees for both trades

        # Calculate gas costs with current market conditions
        # TODO: Implement actual market condition detection
        market_conditions = "normal"  # For now, assume normal conditions
        gas_cost = estimate_gas_cost(pool_type, market_conditions)
        
        # Adjust min profit threshold based on gas costs
        dynamic_profit_threshold = max(min_profit_threshold, gas_cost * 2)
        
        # Calculate net profit
        net_profit = front_run_profit + back_run_profit - pool_fees - gas_cost

        return SimulationResult(
            front_run_profit=front_run_profit,
            back_run_profit=back_run_profit,
            gas_cost=gas_cost,
            pool_fees=pool_fees,
            net_profit=net_profit,
            is_profitable=net_profit >= dynamic_profit_threshold,  # Use dynamic threshold
        )

    except Exception as e:
        logger.error(f"Error simulating sandwich trade: {e}")
        return None
