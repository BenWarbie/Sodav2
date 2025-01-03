"""Unit tests for ray_log_decoder.py"""

import base64

import pytest

from bot.src.ray_log_decoder import (POOL_CONFIGS, calculate_fees,
                                     decode_ray_log, determine_trade_direction,
                                     identify_pool)


@pytest.fixture
def test_ray_log():
    """Example ray_log from a real transaction"""
    return (
        "A9i7rKplAAAAK/4CAAAAAAACAAAAAAAAANi7rKplAAAAmp+7Iy8v"
        "AwDnzAxpCQAAAAuNKwEAAAAA"
    )


@pytest.fixture
def corrupted_ray_log():
    """Invalid base64 data"""
    return "Invalid+Base64+Data=="


@pytest.fixture
def partial_ray_log():
    """Partial ray_log with only amount_in and amount_out"""
    values = [1_000_000_000, 950_000_000]  # 1 SOL in, 0.95 SOL out
    packed = b"".join(val.to_bytes(8, "little") for val in values)
    return base64.b64encode(packed).decode()


def test_decode_ray_log(test_ray_log):
    """Test ray_log decoding functionality."""
    result = decode_ray_log(test_ray_log)
    assert result is not None
    assert "amount_in" in result
    assert "amount_out" in result
    assert "pool_type" in result


def test_decode_ray_log_corrupted(corrupted_ray_log):
    """Test handling of corrupted base64 data."""
    result = decode_ray_log(corrupted_ray_log)
    assert result is None


def test_decode_ray_log_partial(partial_ray_log):
    """Test handling of partial ray_log data."""
    result = decode_ray_log(partial_ray_log)
    assert result is not None
    assert result["amount_in"] == 1_000_000_000
    assert result["amount_out"] == 950_000_000
    assert result["pool_type"] == "SOL/USDC"  # Default pool type


def test_decode_ray_log_empty():
    """Test handling of empty ray_log."""
    result = decode_ray_log("")
    assert result is None


def test_calculate_fees():
    """Test fee calculation."""
    amount = 1_000_000_000  # 1 SOL
    pool_type = "SOL/USDC"

    fee_amount, fee_rate = calculate_fees(amount, pool_type)

    expected_fee_rate = POOL_CONFIGS[pool_type]["fee_rate"]
    expected_fee_amount = int(amount * expected_fee_rate)

    assert fee_amount == expected_fee_amount
    assert fee_rate == expected_fee_rate


def test_identify_pool():
    """Test pool identification."""
    pool_id = 123  # Example pool ID
    pool_type = identify_pool(pool_id)
    assert pool_type in POOL_CONFIGS.keys()


def test_decode_ray_log_with_prefix():
    """Test handling of ray_log with prefix."""
    values = [1234567890, 1_000_000_000, 123456789, 950_000_000]
    packed = b"".join(val.to_bytes(8, "little") for val in values)
    encoded = base64.b64encode(packed).decode()
    prefixed = f"ray_log: {encoded}"

    result = decode_ray_log(prefixed)
    assert result is not None
    assert result["amount_in"] == 1_000_000_000
    assert result["amount_out"] == 950_000_000


def test_decode_ray_log_version_3():
    """Test handling of version 3 format."""
    values = [1234567890, 1_000_000_000, 123456789, 950_000_000]
    packed = bytes([0x03]) + b"".join(val.to_bytes(8, "little") for val in values)
    encoded = base64.b64encode(packed).decode()

    result = decode_ray_log(encoded)
    assert result is not None
    assert result["amount_in"] == 1_000_000_000
    assert result["amount_out"] == 950_000_000


def test_determine_trade_direction_buy():
    """Test buy order detection (SOL -> USDC)."""
    # 2 SOL -> 40 USDC
    # 2 SOL = 2_000_000_000 lamports (9 decimals)
    # 40 USDC = 40_000_000 USDC smallest units (6 decimals)
    direction = determine_trade_direction(
        amount_in=2_000_000_000,  # 2 SOL
        amount_out=40_000_000,  # 40 USDC
        pool_type="SOL/USDC",
    )
    assert direction == "buy"


def test_determine_trade_direction_sell():
    """Test sell order detection (USDC -> SOL)."""
    # 40 USDC -> 2 SOL
    direction = determine_trade_direction(
        amount_in=40_000_000,  # 40 USDC
        amount_out=2_000_000_000,  # 2 SOL
        pool_type="SOL/USDC",
    )
    assert direction == "sell"


def test_determine_trade_direction_unknown_pool():
    """Test handling of unsupported pool types."""
    direction = determine_trade_direction(
        amount_in=1_000_000_000, amount_out=1_000_000_000, pool_type="UNSUPPORTED"
    )
    assert direction == "unknown"


def test_determine_trade_direction_equal_normalized():
    """Test edge case with equal normalized values."""
    # 1 SOL (9 decimals) -> 1 USDC (6 decimals)
    direction = determine_trade_direction(
        amount_in=1_000_000_000,  # 1 SOL
        amount_out=1_000_000,  # 1 USDC
        pool_type="SOL/USDC",
    )
    # Should default to sell when equal
    assert direction == "sell"
