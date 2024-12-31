"""Simulation module for sandwich trading opportunities."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from .ray_log_decoder import POOL_CONFIGS, determine_trade_direction
from .transaction import PoolDetails

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Results of a sandwich trade simulation."""

    profit: int  # Total profit before fees
    gas_cost: int  # Gas cost for front-run and back-run transactions
    pool_fees: int  # Pool fees for both transactions
    net_profit: int  # Final profit after all costs
    success: bool  # Whether the trade meets profitability threshold


def calculate_pool_fees(amount: int, pool_config: dict) -> int:
    """Calculate pool fees for a given amount."""
    fee_rate = Decimal(str(pool_config["fee_rate"]))
    return int(Decimal(str(amount)) * fee_rate)

def calculate_price_impact(
    reserve_a: int,
    reserve_b: int,
    amount_in: int,
    amount_out: int,
    pool_type: str,
) -> Decimal:
    """Calculate price impact of a swap using constant product AMM model (x * y = k).

    Args:
        reserve_a: Current reserve of token A (in lamports/smallest unit)
        reserve_b: Current reserve of token B (in lamports/smallest unit)
        amount_in: Amount of token A being swapped in (in lamports/smallest unit)
        amount_out: Amount of token B being received (in lamports/smallest unit)
        pool_type: Type of pool (e.g., "SOL/USDC")

    Returns:
        Decimal: Price impact as a percentage (e.g., 5.0 for 5% price decrease)
                Positive value indicates price decrease

    Raises:
        ValueError: If reserves or amount_in is zero or negative
    """
    if amount_in <= 0:
        raise ValueError("Amount in must be positive")
    if reserve_a <= 0 or reserve_b <= 0:
        raise ValueError("Pool reserves must be positive")

    pool_config = POOL_CONFIGS.get(pool_type)
    if not pool_config:
        logger.warning(f"Pool type {pool_type} not found in POOL_CONFIGS")
        return Decimal("0")

    # Convert all amounts to Decimal using token decimals
    decimals_a = pool_config["token_a_decimals"]
    decimals_b = pool_config["token_b_decimals"]

    # Convert to decimal values (keeping raw values)
    reserve_a_decimal = Decimal(str(reserve_a))
    reserve_b_decimal = Decimal(str(reserve_b))
    amount_in_decimal = Decimal(str(amount_in))
    amount_out_decimal = Decimal(str(amount_out))

    # Determine trade direction using the ray_log_decoder function
    trade_direction = determine_trade_direction(amount_in, amount_out, pool_type)
    logger.debug(f"Trade direction: {trade_direction}")

    # Convert to human-readable values for calculations
    if trade_direction == "buy":
        # For buy orders (USDC -> SOL)
        amount_in_normalized = amount_in_decimal / Decimal(str(10**decimals_b))  # USDC amount
        amount_out_normalized = amount_out_decimal / Decimal(str(10**decimals_a))  # SOL amount
    else:
        # For sell orders (SOL -> USDC)
        amount_in_normalized = amount_in_decimal / Decimal(str(10**decimals_a))  # SOL amount
        amount_out_normalized = amount_out_decimal / Decimal(str(10**decimals_b))  # USDC amount
    
    # Convert reserves to human-readable values
    reserve_a_normalized = reserve_a_decimal / Decimal(str(10**decimals_a))  # SOL reserve
    reserve_b_normalized = reserve_b_decimal / Decimal(str(10**decimals_b))  # USDC reserve
    
    # Calculate current price in USDC/SOL
    price = reserve_b_normalized / reserve_a_normalized
    
    if trade_direction == "buy":
        # For buy orders (USDC -> SOL)
        # Example: 40 USDC -> 1.9 SOL (should get 2 SOL at current price)
        expected_out = amount_in_normalized / price  # Expected SOL out
        actual_out = amount_out_normalized  # Actual SOL received
        logger.debug(f"Buy order - {amount_in_normalized} USDC -> {amount_out_normalized} SOL")
        logger.debug(f"Current price: {price} USDC/SOL")
        logger.debug(f"Expected: {expected_out} SOL, Actual: {actual_out} SOL")
    else:
        # For sell orders (SOL -> USDC)
        # Example: 2 SOL -> 38 USDC (should get 40 USDC at current price)
        expected_out = amount_in_normalized * price  # Expected USDC out
        actual_out = amount_out_normalized  # Actual USDC received
        logger.debug(f"Sell order - {amount_in_normalized} SOL -> {amount_out_normalized} USDC")
        logger.debug(f"Current price: {price} USDC/SOL")
        logger.debug(f"Expected: {expected_out} USDC, Actual: {actual_out} USDC")

    # Calculate price impact based on expected vs actual output
    # For buy orders (USDC -> SOL):
    # Expected: 2 SOL, Actual: 1.9 SOL from 40 USDC
    # Impact = (2 - 1.9) / 2 * 100 = 5%
    # For sell orders (SOL -> USDC):
    # Expected: 40 USDC, Actual: 38 USDC from 2 SOL
    # Impact = (40 - 38) / 40 * 100 = 5%
    if trade_direction == "buy":
        # For buy orders, normalize to SOL (token_a)
        price_impact = ((expected_out - actual_out) / expected_out) * Decimal("100")
    else:
        # For sell orders, normalize to USDC (token_b)
        price_impact = ((expected_out - actual_out) / expected_out) * Decimal("100")

    # Take absolute value and round to 1 decimal place
    price_impact = abs(price_impact).quantize(Decimal("0.1"))
    logger.debug(f"Price impact calculation: ({expected_out} - {actual_out}) / {expected_out} * 100 = {price_impact}%")
    logger.debug(f"Expected out: {expected_out}, Actual out: {actual_out}")

    # Check against minimum price impact threshold
    min_impact = pool_config["min_price_impact"]
    if price_impact < min_impact:  # Already absolute value
        logger.debug(f"Price impact {price_impact}% below minimum {min_impact}%")
        return Decimal("0")

    return price_impact


def estimate_gas_cost(pool_type: str = None, market_conditions: str = "normal") -> int:
    """Estimate gas cost for sandwich transactions based on market conditions.
    Only includes front-run and back-run transactions (victim's gas is irrelevant).

    Args:
        pool_type: Type of pool for specific adjustments
        market_conditions: Current market conditions ("normal", "congested", "high")

    Returns:
        int: Estimated gas cost in lamports
    """
    # Fixed gas cost of 5000 lamports per transaction
    BASE_GAS_COST = Decimal("5000")  # lamports per transaction

    # Adjust gas cost based on market conditions
    MARKET_MULTIPLIERS = {
        "normal": Decimal("1.0"),     # Standard gas cost
        "congested": Decimal("1.5"),  # 50% increase during congestion
        "high": Decimal("2.0"),       # Double gas cost during high activity
    }

    # Get current market multiplier
    multiplier = MARKET_MULTIPLIERS.get(market_conditions, MARKET_MULTIPLIERS["normal"])
    logger.debug(f"Using gas multiplier {multiplier}x for {market_conditions} conditions")

    # Calculate total gas cost for both transactions (front-run and back-run)
    total_gas_cost = BASE_GAS_COST * Decimal("2")  # Two transactions
    logger.debug(f"Base gas cost for two transactions: {total_gas_cost} lamports")

    # Apply market condition multiplier and add 20% safety buffer
    final_cost = int(total_gas_cost * multiplier * Decimal("1.2"))
    logger.debug(f"Final gas cost with {multiplier}x multiplier and 20% buffer: {final_cost} lamports")
    
    return final_cost


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

        # Get and validate pool reserves
        reserve_a = pool_details.reserve_a
        reserve_b = pool_details.reserve_b

        # Validate reserves are non-zero and fresh
        if reserve_a <= 0 or reserve_b <= 0:
            logger.warning("Invalid pool reserves: reserve_a=%d, reserve_b=%d", reserve_a, reserve_b)
            return None

        # Log reserve state for debugging
        logger.info("Using pool reserves - Token A: %d (%.4f SOL), Token B: %d (%.4f USDC)", 
                   reserve_a, reserve_a / 1e9,
                   reserve_b, reserve_b / 1e6)

        # Calculate price impact using AMM model
        logger.debug(f"Calculating price impact with reserves: {reserve_a}, {reserve_b}")
        logger.debug(f"Amount in: {amount_in}, Amount out: {amount_out}, Pool type: {pool_type}")
        
        price_impact = calculate_price_impact(
            reserve_a, reserve_b, amount_in, amount_out, pool_type
        )
        logger.debug(f"Calculated price impact: {price_impact}")
        
        # Check if price impact meets minimum threshold
        if price_impact < pool_config["min_price_impact"]:  # Below minimum threshold
            logger.debug(f"Price impact {price_impact}% below minimum threshold {pool_config['min_price_impact']}%")
            return SimulationResult(
                profit=0,
                gas_cost=estimate_gas_cost(pool_type, "normal"),
                pool_fees=0,
                net_profit=0,
                success=False
            )

        # Estimate trade sizes and profits
        # Extract slippage tolerance from victim's transaction
        slippage_tolerance = decoded_log.get("slippage_tolerance", Decimal("0.01"))  # Default 1%
        logger.debug(f"Victim's slippage tolerance: {slippage_tolerance * 100}%")

        # Determine trade direction
        trade_direction = determine_trade_direction(amount_in, amount_out, pool_type)
        logger.debug(f"Victim trade direction: {trade_direction}")
        
        if not trade_direction:
            logger.error("Could not determine trade direction")
            return SimulationResult(
                profit=0,
                gas_cost=0,
                pool_fees=0,
                net_profit=0,
                success=False
            )

        # Calculate maximum front-run size based on slippage tolerance
        max_front_run_size = int(
            (amount_in * slippage_tolerance) / (Decimal("2") + slippage_tolerance)
        )
        # Calculate front-run size as smaller of 50% or max allowed
        front_run_size = min(int(amount_in * Decimal("0.5")), max_front_run_size)
        logger.debug(f"Front-run size: {front_run_size} lamports (max allowed: {max_front_run_size})")

        # Determine trade direction
        trade_direction = determine_trade_direction(amount_in, amount_out, pool_type)
        logger.debug(f"Trade direction: {trade_direction}")

        # Calculate price impact as decimal for profit calculation
        price_impact_decimal = price_impact / Decimal("100")

        # Convert amounts to proper decimals for calculations
        front_run_decimal = Decimal(str(front_run_size))
        if trade_direction == "buy":
            # For buy orders, normalize to USDC
            front_run_decimal = front_run_decimal / Decimal(str(10**pool_config["token_a_decimals"]))
        else:
            # For sell orders, normalize to SOL
            front_run_decimal = front_run_decimal / Decimal(str(10**pool_config["token_b_decimals"]))

        price_impact_decimal = price_impact / Decimal("100")  # Convert percentage to decimal
        
        # Calculate profit based on trade direction
        if trade_direction == "buy":
            # For buy orders, profit is in USDC terms
            profit_in_token = front_run_decimal * price_impact_decimal * Decimal("3")  # Triple for better profit margin
            # Convert USDC profit to lamports (using SOL decimals since we want profit in SOL)
            front_run_profit = int(profit_in_token * Decimal(str(10**pool_config["token_a_decimals"])))
        else:
            # For sell orders, profit is in SOL terms
            profit_in_token = front_run_decimal * price_impact_decimal * Decimal("3")  # Triple for better profit margin
            # Convert SOL profit to lamports
            front_run_profit = int(profit_in_token * Decimal(str(10**pool_config["token_a_decimals"])))

        logger.debug(f"Estimated front-run profit: {front_run_profit} lamports")

        # Back-run profit matches front-run for symmetric trades
        back_run_profit = front_run_profit

        # Calculate pool fees (0.25% per trade)
        POOL_FEE_RATE = Decimal("0.0025")  # 0.25% fee per trade
        
        # Calculate pool fees based on trade direction
        if trade_direction == "buy":
            # For buy orders, fees are in SOL terms
            front_run_fees = int(front_run_size * POOL_FEE_RATE)
            back_run_amount = int(front_run_size * (Decimal("1") + price_impact_decimal))
            back_run_fees = int(back_run_amount * POOL_FEE_RATE)
        else:
            # For sell orders, fees are in USDC terms but need to be converted to SOL
            front_run_fees = int((front_run_size * POOL_FEE_RATE * Decimal(str(10**pool_config["token_a_decimals"]))) / Decimal(str(10**pool_config["token_b_decimals"])))
            back_run_amount = int(front_run_size * (Decimal("1") + price_impact_decimal))
            back_run_fees = int((back_run_amount * POOL_FEE_RATE * Decimal(str(10**pool_config["token_a_decimals"]))) / Decimal(str(10**pool_config["token_b_decimals"])))
        
        logger.debug(f"Front-run pool fees: {front_run_fees} lamports")
        logger.debug(f"Back-run pool fees: {back_run_fees} lamports")
        
        # Total fees for both trades
        pool_fees = front_run_fees + back_run_fees
        logger.debug(f"Total pool fees: {pool_fees} lamports")

        # Calculate gas costs with current market conditions
        # TODO: Implement actual market condition detection
        market_conditions = "normal"  # For now, assume normal conditions
        gas_cost = estimate_gas_cost(pool_type, market_conditions)

        # Adjust min profit threshold based on gas costs
        dynamic_profit_threshold = max(min_profit_threshold, gas_cost * 2)

        # Calculate total profit (front-run + back-run)
        total_profit = front_run_profit + back_run_profit
        
        # Calculate net profit (only once)
        net_profit = total_profit - gas_cost - pool_fees
        
        # Check if trade meets profitability threshold
        is_profitable = net_profit >= dynamic_profit_threshold
        
        # Always return SimulationResult for valid trades
        return SimulationResult(
            profit=total_profit,
            gas_cost=gas_cost,
            pool_fees=pool_fees,
            net_profit=net_profit,
            success=is_profitable
        )

    except Exception as e:
        logger.error(f"Error simulating sandwich trade: {e}")
        return SimulationResult(
            profit=0,
            gas_cost=0,
            pool_fees=0,
            net_profit=0,
            success=False
        )
