"""Simulation module for sandwich trading opportunities."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, Tuple

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

def calculate_price_impact(
    amount_in: int,
    amount_out: int,
    pool_type: str
) -> Decimal:
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
    
    amount_in_decimal = Decimal(amount_in) / Decimal(10 ** decimals_in)
    amount_out_decimal = Decimal(amount_out) / Decimal(10 ** decimals_out)
    
    # Price impact = (amount_out_expected - amount_out_actual) / amount_out_expected
    price_impact = (amount_out_decimal - amount_in_decimal) / amount_in_decimal
    return abs(price_impact)

def estimate_gas_cost() -> int:
    """Estimate gas cost for sandwich transactions.
    
    Returns:
        int: Estimated gas cost in lamports
    """
    # Conservative estimate: 10000 compute units * 2 transactions * current price
    return 10_000 * 2 * 1  # 1 micro-lamport per compute unit

def simulate_sandwich(
    decoded_log: Dict,
    pool_details: PoolDetails,
    min_profit_threshold: int = 10_000_000,  # 0.01 SOL
    dry_run: bool = False
) -> Optional[SimulationResult]:
    """Simulate a sandwich trade opportunity.
    
    Args:
        decoded_log: Decoded ray_log data
        pool_details: Pool configuration details
        min_profit_threshold: Minimum profit required (in lamports)
        
    Returns:
        SimulationResult if profitable, None otherwise
    """
    try:
        if not decoded_log or 'amount_in' not in decoded_log:
            return None
            
        pool_type = decoded_log.get('pool_type')
        if not pool_type or pool_type not in POOL_CONFIGS:
            logger.debug(f"Unsupported pool type: {pool_type}")
            return None
            
        pool_config = POOL_CONFIGS[pool_type]
        amount_in = decoded_log['amount_in']
        amount_out = decoded_log['amount_out']
        
        # Skip small transactions
        if amount_in < pool_config['min_amount_threshold']:
            logger.debug(f"Transaction too small: {amount_in} < {pool_config['min_amount_threshold']}")
            return None
            
        # Calculate price impact
        price_impact = calculate_price_impact(amount_in, amount_out, pool_type)
        if price_impact < pool_config['min_price_impact']:
            logger.debug(f"Price impact too low: {price_impact} < {pool_config['min_price_impact']}")
            return None
            
        # Calculate pool fees
        pool_fee_rate = pool_config['fee_rate']
        pool_fees = int(amount_in * pool_fee_rate * 2)  # Fees for both front and back run
        
        # Estimate profits (simplified model)
        # Front-run: Buy tokens before large trade
        front_run_size = amount_in // 4  # 25% of detected trade
        front_run_profit = int(front_run_size * price_impact)
        
        # Back-run: Sell tokens after large trade
        back_run_profit = int(front_run_size * price_impact)
        
        # Calculate gas costs
        gas_cost = estimate_gas_cost()
        
        # Calculate net profit
        net_profit = front_run_profit + back_run_profit - pool_fees - gas_cost
        
        return SimulationResult(
            front_run_profit=front_run_profit,
            back_run_profit=back_run_profit,
            gas_cost=gas_cost,
            pool_fees=pool_fees,
            net_profit=net_profit,
            is_profitable=net_profit >= min_profit_threshold
        )
        
    except Exception as e:
        logger.error(f"Error simulating sandwich trade: {e}")
        return None
