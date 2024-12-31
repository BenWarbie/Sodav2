"""Rate limiter for Quicknode RPC requests."""

import logging
import time
from typing import List

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter for RPC requests."""

    def __init__(self, max_requests: int = 15, window_seconds: int = 1):
        """Initialize the rate limiter.

        Args:
            max_requests: Maximum number of requests per window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.request_timestamps: List[float] = []

    def check_rate_limit(self) -> bool:
        """Check if we're within the rate limit.
        Returns:
            bool: True if we can make a request, False if we're at the limit
        """
        current_time = time.time()

        # Remove timestamps outside the window
        current_window = [
            ts
            for ts in self.request_timestamps
            if current_time - ts < self.window_seconds
        ]
        
        # Update timestamps list with only current window
        self.request_timestamps = current_window

        # Check if we're at the limit
        if len(self.request_timestamps) >= self.max_requests:
            return False

        # Record this request
        self.request_timestamps.append(current_time)
        return True

    def wait_if_needed(self) -> None:
        """Wait if we're at the rate limit."""
        current_time = time.time()

        # Remove timestamps outside the window
        self.request_timestamps = [
            ts
            for ts in self.request_timestamps
            if current_time - ts < self.window_seconds
        ]

        # If we're at the limit, wait until we can make another request
        if len(self.request_timestamps) >= self.max_requests:
            sleep_time = self.request_timestamps[0] + self.window_seconds - current_time
            if sleep_time > 0:
                logger.info("Rate limit reached. Waiting %.2f seconds...", sleep_time)
                time.sleep(sleep_time)
            # Clean up old timestamps after waiting
            self.request_timestamps = self.request_timestamps[1:]

        # Record this request
        self.request_timestamps.append(time.time())

    async def async_wait_if_needed(self) -> None:
        """Async version of wait_if_needed."""
        import asyncio

        current_time = time.time()

        # Remove timestamps outside the window
        self.request_timestamps = [
            ts
            for ts in self.request_timestamps
            if current_time - ts < self.window_seconds
        ]

        # If we're at the limit, wait until we can make another request
        if len(self.request_timestamps) >= self.max_requests:
            sleep_time = self.request_timestamps[0] + self.window_seconds - current_time
            if sleep_time > 0:
                logger.info("Rate limit reached. Waiting %.2f seconds...", sleep_time)
                await asyncio.sleep(sleep_time)
            # Clean up old timestamps after waiting
            self.request_timestamps = self.request_timestamps[1:]

        # Record this request
        self.request_timestamps.append(time.time())
