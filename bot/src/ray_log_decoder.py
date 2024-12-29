"""Decoder for Raydium AMM ray_log data."""

import base64
import struct
import logging
from typing import Dict, Optional, Tuple
from decimal import Decimal

logger = logging.getLogger(__name__)

# Constants for token decimals
SOL_DECIMALS = 9
USDC_DECIMALS = 6
USDT_DECIMALS = 6

# Known token mint addresses
TOKEN_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
}

# Raydium pool configurations
POOL_CONFIGS = {
    "SOL/USDC": {
        "min_amount_threshold": 1_000_000_000,  # 1 SOL
        "min_price_impact": Decimal("0.01"),    # 1%
        "max_slippage": Decimal("0.02"),        # 2%
        "fee_rate": Decimal("0.003"),           # 0.3%
        "token_a_decimals": SOL_DECIMALS,
        "token_b_decimals": USDC_DECIMALS
    },
    "SOL/USDT": {
        "min_amount_threshold": 1_000_000_000,  # 1 SOL
        "min_price_impact": Decimal("0.01"),    # 1%
        "max_slippage": Decimal("0.02"),        # 2%
        "fee_rate": Decimal("0.003"),           # 0.3%
        "token_a_decimals": SOL_DECIMALS,
        "token_b_decimals": USDT_DECIMALS
    }
}

def identify_pool(pool_id: int) -> Optional[str]:
    """Identify the pool type based on pool ID."""
    # TODO: Implement pool identification logic
    # For now, assume SOL/USDC pool
    return "SOL/USDC"

def calculate_fees(amount: int, pool_type: str) -> Tuple[int, Decimal]:
    """Calculate transaction fees and get pool fee rate."""
    pool_config = POOL_CONFIGS.get(pool_type)
    if not pool_config:
        return 0, Decimal("0")
        
    fee_rate = pool_config["fee_rate"]
    fee_amount = int(amount * fee_rate)
    return fee_amount, fee_rate

def decode_ray_log(ray_log: str) -> Optional[Dict]:
    """Decode a ray_log message from Raydium AMM.
    
    Args:
        ray_log: Base64 encoded ray_log data
        
    Returns:
        Dictionary containing decoded swap parameters if successful
    """
    try:
        # Remove "ray_log: " prefix if present
        if ray_log.startswith("ray_log: "):
            ray_log = ray_log[9:]
            
        # Decode base64 data
        decoded = base64.b64decode(ray_log)
        logger.debug(f"Decoded ray_log bytes: {decoded.hex()}")
        logger.debug(f"Length: {len(decoded)} bytes")
        
        # Determine format based on data length
        data_len = len(decoded)
        logger.debug(f"Data length: {data_len} bytes")
        logger.debug(f"Raw decoded data (hex): {decoded.hex()}")
        logger.debug(f"Raw decoded data (bytes): {list(decoded)}")
        
        # Check if we have a version byte
        if data_len == 57 and decoded[0] in [0x03]:  # Version 3 format
            logger.debug(f"Detected version {decoded[0]} format")
            # Skip the version byte
            decoded = decoded[1:]
            data_len = len(decoded)
            
        if data_len == 56:  # 7 u64 values (test data format)
            try:
                values = struct.unpack('<QQQQQQQ', decoded)
                logger.debug(f"Decoded as 7xu64: {values}")
                
                return {
                    'timestamp_in': values[0],
                    'amount_in': values[1],
                    'pool_id': values[2],
                    'pool_type': identify_pool(values[2]),
                    'timestamp_out': values[3],
                    'amount_out': values[4],
                    'pool_token': values[5],
                    'extra_data': values[6]
                }
            except struct.error as e:
                logger.error(f"Failed to unpack as 7xu64: {e}")
                logger.debug(f"Failed data (hex): {decoded.hex()}")
                logger.debug(f"Expected format: 7 unsigned 64-bit integers")
                # Don't return None yet, let it try other formats
                
        elif data_len == 48:  # 6 u64 values
            try:
                values = struct.unpack('<QQQQQQ', decoded)
                logger.debug(f"Decoded as 6xu64: {values}")
                
                pool_id = values[2]
                pool_type = identify_pool(pool_id)
                
                return {
                    'timestamp_in': values[0],
                    'amount_in': values[1],
                    'pool_id': pool_id,
                    'pool_type': pool_type,
                    'timestamp_out': values[3], 
                    'amount_out': values[4],
                    'pool_token': values[5]
                }
            except struct.error as e:
                logger.error(f"Failed to unpack as 6xu64: {e}")
                
                
        elif data_len == 32:  # 4 u64 values
            try:
                values = struct.unpack('<QQQQ', decoded)
                logger.debug(f"Decoded as 4xu64: {values}")
                
                return {
                    'timestamp_in': values[0],
                    'amount_in': values[1],
                    'amount_out': values[2],
                    'pool_id': values[3],
                    'pool_type': identify_pool(values[3])
                }
            except struct.error as e:
                logger.error(f"Failed to unpack as 4xu64: {e}")
                
        elif data_len == 24:  # 3 u64 values
            try:
                values = struct.unpack('<QQQ', decoded)
                logger.debug(f"Decoded as 3xu64: {values}")
                
                return {
                    'amount_in': values[0],
                    'amount_out': values[1],
                    'pool_id': values[2],
                    'pool_type': identify_pool(values[2])
                }
            except struct.error as e:
                logger.error(f"Failed to unpack as 3xu64: {e}")
        
        # Log the hex representation for debugging
        logger.debug(f"Raw data hex: {decoded.hex()}")
        
        # Try to interpret as a sequence of u64s
        try:
            num_u64s = data_len // 8
            if data_len % 8 == 0 and num_u64s > 0:
                values = struct.unpack(f'<{"Q"*num_u64s}', decoded)
                logger.debug(f"Decoded as {num_u64s}xu64: {values}")
                
                # Return at least amount_in and amount_out if we have them
                if num_u64s >= 2:
                    return {
                        'amount_in': values[0],
                        'amount_out': values[1],
                        'pool_type': 'SOL/USDC',  # Default to SOL/USDC
                        'extra_values': values[2:] if len(values) > 2 else []
                    }
        except struct.error as e:
            logger.error(f"Failed to unpack as u64 sequence: {e}")
            
        # Final fallback to u32 values
        try:
            values = struct.unpack(f'<{"L"*(data_len//4)}', decoded)
            logger.debug(f"Decoded as u32: {values}")
            
            return {
                'amount_in': values[0] | (values[1] << 32) if len(values) > 1 else values[0],
                'amount_out': values[2] | (values[3] << 32) if len(values) > 3 else values[2],
                'pool_type': 'SOL/USDC'  # Default to SOL/USDC for now
            }
        except struct.error as e:
            logger.error(f"Failed to unpack as u32: {e}")
            
    except Exception as e:
        logger.error(f"Failed to decode ray_log: {e}")
        return None

if __name__ == '__main__':
    # Test with example ray_log
    test_log = 'A9i7rKplAAAAK/4CAAAAAAACAAAAAAAAANi7rKplAAAAmp+7Iy8vAwDnzAxpCQAAAAuNKwEAAAAA'
    
    logging.basicConfig(level=logging.DEBUG)
    result = decode_ray_log(test_log)
    
    if result:
        print("\nDecoded ray_log data:")
        for key, value in result.items():
            print(f"{key}: {value}")
