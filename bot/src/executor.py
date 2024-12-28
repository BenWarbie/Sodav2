"""Transaction executor for MEV sandwich bot."""

import logging
import asyncio
import random
import time
from typing import List, Optional

from solana.rpc.async_api import AsyncClient
from solders.transaction import Transaction
from solders.keypair import Keypair
from solders.signature import Signature
from solders.hash import Hash
from solana.rpc.commitment import Processed, Confirmed, Finalized

from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

class TransactionExecutor:
    """Executes sandwich attack transactions."""
    
    def __init__(self, client: AsyncClient, payer: Keypair):
        """Initialize the transaction executor.
        
        Args:
            client: Solana RPC client
            payer: Keypair that will pay for and sign transactions
        """
        self.client = client
        self.payer = payer
        self.rate_limiter = RateLimiter()
        
    async def execute_sandwich(
        self,
        front_tx: Transaction,
        back_tx: Transaction,
        max_retries: int = 3,
        dry_run: bool = False
    ) -> Optional[List[str]]:
        """Execute a sandwich attack by sending front-run and back-run transactions.
        
        Args:
            front_tx: Front-run transaction
            back_tx: Back-run transaction
            max_retries: Maximum number of retry attempts per transaction
            
        Returns:
            List of transaction signatures if successful, None if failed
        """
        try:
            if dry_run:
                logger.info("DRY RUN: Simulating sandwich attack execution")
                # Simulate transaction signatures
                front_sig = f"DRY_RUN_FRONT_{int(time.time())}"
                back_sig = f"DRY_RUN_BACK_{int(time.time())}"
                
                # Log simulated execution
                logger.info(f"Simulated transaction (not valid on Explorer): {front_sig}")
                logger.info(f"Simulated transaction (not valid on Explorer): {back_sig}")
                logger.info("NOTE: These are simulated transactions and cannot be viewed on Explorer")
                
                return [front_sig, back_sig]
            
            # Send front-run transaction
            front_sig = await self._send_transaction(front_tx, max_retries)
            if not front_sig:
                logger.error("Failed to send front-run transaction")
                return None
                
            # Send back-run transaction
            back_sig = await self._send_transaction(back_tx, max_retries)
            if not back_sig:
                logger.error("Failed to send back-run transaction")
                return None
                
            return [front_sig, back_sig]
            
        except Exception as e:
            logger.error(f"Failed to execute sandwich attack: {e}")
            return None
            
    async def _send_transaction(
        self,
        transaction: Transaction,
        max_retries: int,
        initial_backoff: float = 1.0
    ) -> Optional[str]:
        """Send a transaction with retries and rate limit handling.
        
        Args:
            transaction: Transaction to send
            max_retries: Maximum number of retry attempts
            initial_backoff: Initial backoff delay in seconds
            
        Returns:
            Transaction signature if successful, None if failed
        """
        for attempt in range(max_retries):
            try:
                # Add backoff delay for retries
                if attempt > 0:
                    delay = initial_backoff * (2 ** attempt) + random.uniform(0, 1)
                    logger.info(f"Retrying transaction in {delay:.2f} seconds (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)

                # Create transaction options with proper parameter names
                opts = {
                    "skipPreflight": False,
                    "encoding": "base64",
                    "maxRetries": 3
                }
                
                try:
                    # Get recent blockhash with rate limiting
                    for blockhash_attempt in range(3):
                        try:
                            await self.rate_limiter.async_wait_if_needed()
                            blockhash = await self.client.get_latest_blockhash()
                            # No need to set blockhash here as it's set during Transaction creation
                            break
                        except Exception as e:
                            if "429" in str(e) and blockhash_attempt < 2:
                                await asyncio.sleep(1 * (2 ** blockhash_attempt))
                                continue
                            raise

                    # Send transaction with rate limiting
                    await self.rate_limiter.async_wait_if_needed()
                    # Transaction is already signed, just serialize and send
                    serialized_tx = bytes(transaction)
                    result = await self.client.send_transaction(
                        serialized_tx,
                        opts=opts
                    )
                    # Get signature from transaction result
                    if hasattr(result.value, 'signature'):
                        signature = result.value.signature
                    else:
                        # Handle case where result.value is the signature itself
                        signature = str(result.value)
                    
                    # Add delay before confirmation check
                    await asyncio.sleep(0.5)
                    
                    # Wait for confirmation with backoff
                    for confirm_attempt in range(3):
                        try:
                            await self.rate_limiter.async_wait_if_needed()
                            await self.client.confirm_transaction(
                                signature,
                                commitment=Confirmed
                            )
                            logger.info(f"Transaction confirmed: https://explorer.solana.com/tx/{signature}?cluster=devnet")
                            return signature
                        except Exception as e:
                            if confirm_attempt < 2:
                                if "429" in str(e):
                                    await asyncio.sleep(1 * (2 ** confirm_attempt))
                                    continue
                                elif "not found" in str(e).lower():
                                    # Transaction might still be processing
                                    await asyncio.sleep(1)
                                    continue
                            raise
                    
                except Exception as e:
                    if "429" in str(e):
                        logger.warning(f"Rate limit hit: {str(e)}")
                        continue
                    logger.error(f"Failed to send or confirm transaction: {e}")
                    if attempt == max_retries - 1:
                        return None
                    continue
                
            except Exception as e:
                logger.warning(f"Transaction attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error("Max retries reached")
                    return None
                    
        return None
