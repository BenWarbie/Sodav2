import pytest
import time
from unittest.mock import patch, MagicMock

from bot.src.rate_limiter import RateLimiter

def test_rate_limiter_respects_limit():
    rate_limiter = RateLimiter(max_requests=15, time_window=1.0)
    
    # Make 15 requests rapidly
    start_time = time.time()
    for _ in range(15):
        assert rate_limiter.check_rate_limit() is True
        
    # 16th request should be blocked
    assert rate_limiter.check_rate_limit() is False
    
    # Wait for time window to pass
    time.sleep(1.0 - (time.time() - start_time))
    
    # Should allow requests again
    assert rate_limiter.check_rate_limit() is True

def test_rate_limiter_sliding_window():
    rate_limiter = RateLimiter(max_requests=15, time_window=1.0)
    
    # Make 10 requests
    for _ in range(10):
        assert rate_limiter.check_rate_limit() is True
        
    time.sleep(0.5)  # Wait half window
    
    # Make 5 more requests
    for _ in range(5):
        assert rate_limiter.check_rate_limit() is True
        
    # Should block additional requests
    assert rate_limiter.check_rate_limit() is False
    
    # Wait for first batch to expire
    time.sleep(0.5)
    
    # Should allow new requests
    assert rate_limiter.check_rate_limit() is True

@patch('time.time')
def test_rate_limiter_cleanup(mock_time):
    rate_limiter = RateLimiter(max_requests=15, time_window=1.0)
    
    # Simulate requests over time
    current_time = 1000.0
    mock_time.return_value = current_time
    
    # Make initial requests
    for _ in range(15):
        assert rate_limiter.check_rate_limit() is True
        
    # Move time forward
    current_time += 1.1
    mock_time.return_value = current_time
    
    # Old requests should be cleaned up
    assert len(rate_limiter.request_times) == 0
    assert rate_limiter.check_rate_limit() is True
