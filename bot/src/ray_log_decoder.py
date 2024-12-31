"""Decoder for Raydium AMM ray_log data."""

import base64
import logging
import struct
from decimal import Decimal
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Raydium AMM Program ID
RAYDIUM_AMM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

logger = logging.getLogger(__name__)

# Constants for token decimals
SOL_DECIMALS = 9
USDC_DECIMALS = 6
USDT_DECIMALS = 6

# Known token mint addresses
TOKEN_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

# Raydium pool configurations
POOL_CONFIGS = {
    "SOL/USDC": {
        "min_amount_threshold": 1_000_000_000,  # 1 SOL
        "min_price_impact": Decimal("0.01"),  # 1%
        "max_slippage": Decimal("0.02"),  # 2%
        "fee_rate": Decimal("0.003"),  # 0.3%
        "token_a_decimals": SOL_DECIMALS,
        "token_b_decimals": USDC_DECIMALS,
    },
    "SOL/USDT": {
        "min_amount_threshold": 1_000_000_000,  # 1 SOL
        "min_price_impact": Decimal("0.01"),  # 1%
        "max_slippage": Decimal("0.02"),  # 2%
        "fee_rate": Decimal("0.003"),  # 0.3%
        "token_a_decimals": SOL_DECIMALS,
        "token_b_decimals": USDT_DECIMALS,
    },
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


def determine_trade_direction(amount_in: int, amount_out: int, pool_type: str) -> str:
    """Determine if a trade is buying or selling SOL based on normalized amounts.
    
    Args:
        amount_in: Input amount in lamports/smallest unit
        amount_out: Output amount in lamports/smallest unit
        pool_type: Type of pool (e.g., "SOL/USDC")
        
    Returns:
        str: "buy" if trading SOL for USDC/USDT, "sell" if trading USDC/USDT for SOL,
             "unknown" for unsupported pool types
    """
    if pool_type not in POOL_CONFIGS:
        logger.warning(f"Unsupported pool type: {pool_type}")
        return "unknown"
        
    pool_config = POOL_CONFIGS[pool_type]
    token_a_decimals = pool_config["token_a_decimals"]  # SOL decimals
    token_b_decimals = pool_config["token_b_decimals"]  # USDC/USDT decimals
    
    # Normalize amounts to account for decimal differences
    normalized_in = Decimal(str(amount_in)) / Decimal(str(10 ** token_a_decimals))
    normalized_out = Decimal(str(amount_out)) / Decimal(str(10 ** token_b_decimals))
    
    if pool_type in ["SOL/USDC", "SOL/USDT"]:
        # Buy: Trading USDC/USDT (token_b) for SOL (token_a)
        # Example: 40 USDC -> 1.9 SOL
        # - amount_in would be 40_000_000 (6 decimals)
        # - amount_out would be 1_900_000_000 (9 decimals)
        # Sell: Trading SOL (token_a) for USDC/USDT (token_b)
        # Example: 2 SOL -> 38 USDC
        # - amount_in would be 2_000_000_000 (9 decimals)
        # - amount_out would be 38_000_000 (6 decimals)
        
        # Compare normalized amounts to determine direction
        # For SOL/USDC:
        # Buy: USDC (6 decimals) -> SOL (9 decimals)
        # Example: 40 USDC (40_000_000) -> 1.9 SOL (1_900_000_000)
        # Sell: SOL (9 decimals) -> USDC (6 decimals)
        # Example: 2 SOL (2_000_000_000) -> 38 USDC (38_000_000)
        
        # Scale amounts to same decimal places (9) for comparison
        scaled_in = Decimal(str(amount_in)) * Decimal(str(10 ** (9 - token_b_decimals)))
        scaled_out = Decimal(str(amount_out))
        
        logger.debug(f"Scaled amounts - In: {scaled_in}, Out: {scaled_out}")
        
        # If scaled input is smaller than output, it's a buy (USDC -> SOL)
        # If scaled input is larger than output, it's a sell (SOL -> USDC)
        if scaled_in < scaled_out:
            return "buy"
        else:
            return "sell"
            
    return "unknown"


def decode_ray_log(ray_log: str, signature: Optional[str] = None) -> Optional[Dict]:
    """Decode a ray_log message from Raydium AMM.

    Args:
        ray_log: Base64 encoded ray_log data
        signature: Optional transaction signature for Explorer links

    Returns:
        Dictionary containing decoded swap parameters if successful:
        {
            'amount_in': int,      # Input amount in lamports
            'amount_out': int,     # Output amount in lamports
            'pool_type': str,      # Pool identifier (e.g., "SOL/USDC")
            'pool_id': str,        # Pool address
            'signature': str,      # Transaction signature (if provided)
        }
    """
    try:
        # Remove "ray_log: " prefix if present
        if ray_log.startswith("ray_log: "):
            ray_log = ray_log[9:]

        # Decode base64 data
        decoded = base64.b64decode(ray_log)
        logger.debug("Decoded ray_log bytes: %s", decoded.hex())
        logger.debug("Length: %d bytes", len(decoded))

        # Determine format based on data length
        data_len = len(decoded)
        logger.debug("Data length: %d bytes", data_len)
        logger.debug("Raw decoded data (hex): %s", decoded.hex())
        logger.debug("Raw decoded data (bytes): %s", list(decoded))

        # Check if we have a version byte
        if data_len > 0 and decoded[0] in [0x03]:  # Version 3 format
            logger.debug("Detected version %d format", decoded[0])
            # Skip the version byte
            decoded = decoded[1:]
            data_len = len(decoded)

        if data_len == 56:  # 7 u64 values (test data format)
            try:
                values = struct.unpack("<QQQQQQQ", decoded)
                logger.debug("Decoded as 7xu64: %s", values)

                return {
                    "timestamp_in": values[0],
                    "amount_in": values[1],
                    "pool_id": values[2],
                    "pool_type": identify_pool(values[2]),
                    "timestamp_out": values[3],
                    "amount_out": values[4],
                    "pool_token": values[5],
                    "extra_data": values[6],
                }
            except struct.error as e:
                logger.error("Failed to unpack as 7xu64: %s", e)
                logger.debug("Failed data (hex): %s", decoded.hex())
                logger.debug("Expected format: 7 unsigned 64-bit integers")
                # Don't return None yet, let it try other formats

        elif data_len == 48:  # 6 u64 values
            try:
                values = struct.unpack("<QQQQQQ", decoded)
                logger.debug("Decoded as 6xu64: %s", values)

                pool_id = values[2]
                pool_type = identify_pool(pool_id)

                return {
                    "timestamp_in": values[0],
                    "amount_in": values[1],
                    "pool_id": pool_id,
                    "pool_type": pool_type,
                    "timestamp_out": values[3],
                    "amount_out": values[4],
                    "pool_token": values[5],
                }
            except struct.error as e:
                logger.error("Failed to unpack as 6xu64: %s", e)

        elif data_len == 32:  # 4 u64 values
            try:
                values = struct.unpack("<QQQQ", decoded)
                logger.debug("Decoded as 4xu64: %s", values)

                return {
                    "timestamp": values[0],
                    "amount_in": values[1],
                    "pool_id": values[2],
                    "amount_out": values[3],
                    "pool_type": identify_pool(values[2]),
                }
            except struct.error as e:
                logger.error("Failed to unpack as 4xu64: %s", e)

        elif data_len == 24:  # 3 u64 values
            try:
                values = struct.unpack("<QQQ", decoded)
                logger.debug("Decoded as 3xu64: %s", values)

                return {
                    "amount_in": values[0],
                    "amount_out": values[1],
                    "pool_id": values[2],
                    "pool_type": identify_pool(values[2]),
                }
            except struct.error as e:
                logger.error("Failed to unpack as 3xu64: %s", e)

        # Log the hex representation for debugging
        logger.debug("Raw data hex: %s", decoded.hex())

        # Try to interpret as a sequence of u64s
        try:
            num_u64s = data_len // 8
            if data_len % 8 == 0 and num_u64s > 0:
                values = struct.unpack(f'<{"Q"*num_u64s}', decoded)
                logger.debug("Decoded as %dxu64: %s", num_u64s, values)

                # For version 3 and prefixed logs with 4 values
                if num_u64s == 4:
                    # Values order: [timestamp, amount_in, pool_id, amount_out]
                    return {
                        "timestamp": values[0],
                        "amount_in": values[1],
                        "pool_id": values[2],
                        "amount_out": values[3],
                        "pool_type": "SOL/USDC",  # Default to SOL/USDC
                    }
                # For standard format with 2 values
                elif num_u64s == 2:
                    return {
                        "amount_in": values[0],
                        "amount_out": values[1],
                        "pool_type": "SOL/USDC",  # Default to SOL/USDC
                    }

                # Return at least amount_in and amount_out if we have them
                if num_u64s >= 2:
                    return {
                        "amount_in": values[0],
                        "amount_out": values[1],
                        "pool_type": "SOL/USDC",  # Default to SOL/USDC
                        "extra_values": values[2:] if len(values) > 2 else [],
                    }
        except struct.error as e:
            logger.error("Failed to unpack as u64 sequence: %s", e)

        # Final fallback to u32 values
        try:
            values = struct.unpack(f'<{"L"*(data_len//4)}', decoded)
            logger.debug("Decoded as u32: %s", values)

            return {
                "amount_in": (
                    values[0] | (values[1] << 32) if len(values) > 1 else values[0]
                ),
                "amount_out": (
                    values[2] | (values[3] << 32) if len(values) > 3 else values[2]
                ),
                "pool_type": "SOL/USDC",  # Default to SOL/USDC for now
            }
        except struct.error as e:
            logger.error("Failed to unpack as u32: %s", e)

    except Exception as e:
        logger.error("Unexpected error decoding ray_log: %s", e)
        # Try each format one last time
        formats = [("<QQQQQQQ", 7), ("<QQQQQQ", 6), ("<QQQQ", 4), ("<QQQ", 3)]
        for fmt, expected_values in formats:
            try:
                if len(decoded) == expected_values * 8:  # 8 bytes per u64
                    values = struct.unpack(fmt, decoded)
                    logger.debug("Successfully decoded with format %s", fmt)
                    return {
                        "amount_in": values[1] if len(values) > 1 else values[0],
                        "amount_out": values[4] if len(values) > 4 else values[1],
                        "pool_type": "SOL/USDC",
                    }
            except struct.error:
                continue

        # Final attempt with dynamic format
        try:
            num_u64s = len(decoded) // 8
            if num_u64s >= 2:  # At least need amount_in and amount_out
                values = struct.unpack(f'<{"Q"*num_u64s}', decoded)
                return {
                    "amount_in": values[0],
                    "amount_out": values[1],
                    "pool_type": "SOL/USDC",
                }
        except struct.error:
            pass

        return None  # All attempts failed
    finally:
        # Log validation summary for cross-checking
        if "values" in locals():
            logger.info("\n=== Ray Log Validation Summary ===")
            logger.info(
                "Amount In: %s lamports", values[0] if len(values) > 0 else "N/A"
            )
            logger.info(
                "Amount Out: %s lamports", values[2] if len(values) > 3 else "N/A"
            )
            logger.info("Pool Type: SOL/USDC")
            if signature:
                logger.info(
                    "Explorer URL: https://explorer.solana.com/tx/%s?cluster=devnet",
                    signature,
                )


if __name__ == "__main__":
    # Test with example ray_log
    test_log = (
        "A9i7rKplAAAAK/4CAAAAAAACAAAAAAAAANi7rKplAAAAmp+7Iy8vAwDnzAxpCQAAAAuNKwEAAAAA"
    )

    logging.basicConfig(level=logging.DEBUG)
    result = decode_ray_log(test_log)

    if result:
        print("\nDecoded ray_log data:")
        for key, value in result.items():
            print(f"{key}: {value}")
