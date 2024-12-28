"""Unit tests for ray_log_decoder.py"""

import unittest
from decimal import Decimal
from src.ray_log_decoder import (
    decode_ray_log,
    analyze_swap_opportunity,
    calculate_fees,
    identify_pool,
    POOL_CONFIGS
)

class TestRayLogDecoder(unittest.TestCase):
    def setUp(self):
        # Example ray_log from a real transaction
        self.test_ray_log = "A9i7rKplAAAAK/4CAAAAAAACAAAAAAAAANi7rKplAAAAmp+7Iy8vAwDnzAxpCQAAAAuNKwEAAAAA"
        
    def test_decode_ray_log(self):
        """Test ray_log decoding functionality."""
        result = decode_ray_log(self.test_ray_log)
        self.assertIsNotNone(result)
        self.assertIn('amount_in', result)
        self.assertIn('amount_out', result)
        self.assertIn('pool_type', result)
        
    def test_calculate_fees(self):
        """Test fee calculation."""
        amount = 1_000_000_000  # 1 SOL
        pool_type = "SOL/USDC"
        
        fee_amount, fee_rate = calculate_fees(amount, pool_type)
        
        expected_fee_rate = POOL_CONFIGS[pool_type]["fee_rate"]
        expected_fee_amount = int(amount * expected_fee_rate)
        
        self.assertEqual(fee_amount, expected_fee_amount)
        self.assertEqual(fee_rate, expected_fee_rate)
        
    def test_identify_pool(self):
        """Test pool identification."""
        pool_id = 123  # Example pool ID
        pool_type = identify_pool(pool_id)
        self.assertIn(pool_type, POOL_CONFIGS.keys())
        
    def test_analyze_swap_opportunity_small_amount(self):
        """Test that small transactions are rejected."""
        decoded_log = {
            'amount_in': 100_000,  # Very small amount
            'amount_out': 95_000,
            'pool_type': 'SOL/USDC',
            'timestamp_in': 123456789
        }
        
        result = analyze_swap_opportunity(decoded_log)
        self.assertIsNone(result)
        
    def test_analyze_swap_opportunity_profitable(self):
        """Test detection of profitable opportunity."""
        # Create a transaction with 2% price impact
        amount_in = 5_000_000_000  # 5 SOL
        price_impact = Decimal('0.02')
        amount_out = int(amount_in * (1 + price_impact))
        
        decoded_log = {
            'amount_in': amount_in,
            'amount_out': amount_out,
            'pool_type': 'SOL/USDC',
            'timestamp_in': 123456789
        }
        
        result = analyze_swap_opportunity(decoded_log)
        
        self.assertIsNotNone(result)
        self.assertGreater(result['net_profit'], 0)
        self.assertIn('fees', result)
        self.assertIn('effective_price_impact', result)
        
    def test_analyze_swap_opportunity_unprofitable(self):
        """Test rejection of unprofitable opportunity."""
        # Create a transaction with 0.1% price impact (below threshold)
        amount_in = 1_000_000_000  # 1 SOL
        price_impact = Decimal('0.001')
        amount_out = int(amount_in * (1 + price_impact))
        
        decoded_log = {
            'amount_in': amount_in,
            'amount_out': amount_out,
            'pool_type': 'SOL/USDC',
            'timestamp_in': 123456789
        }
        
        result = analyze_swap_opportunity(decoded_log)
        self.assertIsNone(result)
        
    def test_analyze_swap_opportunity_invalid_pool(self):
        """Test handling of invalid pool type."""
        decoded_log = {
            'amount_in': 1_000_000_000,
            'amount_out': 1_020_000_000,
            'pool_type': 'INVALID/POOL',
            'timestamp_in': 123456789
        }
        
        result = analyze_swap_opportunity(decoded_log)
        self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()
