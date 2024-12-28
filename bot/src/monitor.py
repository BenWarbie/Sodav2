"""Transaction monitor for detecting swap opportunities on Solana."""

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Tuple
import base64
import struct
from collections import deque
from datetime import datetime, timedelta

import websockets
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .config import (
    DEVNET_WS_URL,
    DEVNET_HTTP_URL,
    RAYDIUM_AMM_PROGRAM_ID,
    SUBSCRIPTION_ID,
    TOKEN_PROGRAM_ID,
    load_keypair,
)
from .ray_log_decoder import decode_ray_log, analyze_swap_opportunity
from .transaction import TransactionBuilder, PoolDetails
from .executor import TransactionExecutor

# Constants
SYSTEM_PROGRAM = "11111111111111111111111111111111"
MAX_REQUESTS_PER_SECOND = 15
REQUEST_WINDOW = 1  # seconds

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Set websockets logger to WARNING to reduce noise
logging.getLogger('websockets').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

class TransactionMonitor:
    """Monitors Solana transactions for swap opportunities."""
    
    def __init__(self, payer: Keypair, dry_run: bool = True):
        """Initialize the transaction monitor.
        
        Args:
            payer: Keypair that will pay for and sign transactions
            dry_run: If True, run in simulation mode without executing trades
        """
        self.client = AsyncClient(DEVNET_HTTP_URL)
        self.subscription_id = SUBSCRIPTION_ID
        self.payer = payer
        self.dry_run = dry_run
        self.pool_details = PoolDetails(
            amm_id=Pubkey.from_string(RAYDIUM_AMM_PROGRAM_ID),
            token_program=Pubkey.from_string(TOKEN_PROGRAM_ID),
            # Using SOL-USDC pool for testing
            token_a_account=Pubkey.from_string("9wFFyRfZBsuAha4YcuxcXLKwMxJR43S7fPfQLusDBzvT"),  # SOL
            token_b_account=Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),  # USDC
            pool_token_mint=Pubkey.from_string("8HoQnePLqPj4M7PUDzfw8e3Ymdwgc7NLGnaTUapubyvu"),
            fee_account=Pubkey.from_string("4Zc4kQZhRQeGztihvcGSWezJE1k44kKEgPCAkdeBfras")
        )
        self.builder = TransactionBuilder(self.client, self.payer, self.pool_details)
        self.executor = TransactionExecutor(self.client, self.payer)
        
        # Trading configuration
        self.min_trade_size = 1_000_000_000  # 1 SOL minimum for live trades
        self.total_profit = 0
        
        # Rate limiting
        self.request_timestamps = deque(maxlen=MAX_REQUESTS_PER_SECOND)
        self.last_rate_log = time.time()
        self.request_count = 0
        self.rate_limit_hits = 0
        
        # Monitoring stats
        self.start_time = datetime.now()
        self.total_requests = 0
        self.total_opportunities = 0
        self.successful_opportunities = 0
        self.missed_opportunities = 0
        
    async def subscribe_to_program_logs(self) -> Dict:
        """Create a subscription request for program logs."""
        if not self.check_rate_limit():
            logger.warning("Rate limit reached, delaying subscription")
            await asyncio.sleep(1)
            return None
            
        logger.info(f"Setting up subscriptions for System Program and Raydium AMM")
        logger.info(f"System Program: {SYSTEM_PROGRAM}")
        logger.info(f"Raydium AMM: {RAYDIUM_AMM_PROGRAM_ID}")
        
        return {
            "jsonrpc": "2.0",
            "id": self.subscription_id,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [RAYDIUM_AMM_PROGRAM_ID]},
                {"commitment": "confirmed"}
            ]
        }
    
    def check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        
        # Remove timestamps older than our window
        while self.request_timestamps and now - self.request_timestamps[0] > REQUEST_WINDOW:
            self.request_timestamps.popleft()
            
        # Check if we're at the limit
        if len(self.request_timestamps) >= MAX_REQUESTS_PER_SECOND:
            self.rate_limit_hits += 1
            return False
            
        # Add current timestamp
        self.request_timestamps.append(now)
        self.total_requests += 1
        
        # Log request rates periodically
        if now - self.last_rate_log >= 60:  # Log every minute
            requests_per_second = len(self.request_timestamps) / REQUEST_WINDOW
            uptime = datetime.now() - self.start_time
            logger.info(f"Monitor Stats:")
            logger.info(f"  Requests/second: {requests_per_second:.2f}")
            logger.info(f"  Total requests: {self.total_requests}")
            logger.info(f"  Rate limit hits: {self.rate_limit_hits}")
            logger.info(f"  Total opportunities: {self.total_opportunities}")
            logger.info(f"  Uptime: {uptime}")
            self.last_rate_log = now
            
        return True
        
    async def process_log(self, log_msg: Dict) -> Optional[Dict]:
        """Process incoming log messages with detailed transaction information."""
        if not self.check_rate_limit():
            logger.warning("Rate limit reached, skipping log processing")
            await asyncio.sleep(1)
            return None
            
        try:
            # Handle subscription confirmation message
            if "result" in log_msg and isinstance(log_msg["result"], int):
                logger.info(f"WebSocket connection established - Subscription ID: {log_msg['result']}")
                logger.info("Monitoring System Program and Raydium AMM transactions on Solana Devnet...")
                return None

            # Parse the message
            if isinstance(log_msg, str):
                log_msg = json.loads(log_msg)

            # Handle actual log messages
            if "params" not in log_msg:
                return None

            result = log_msg.get("params", {}).get("result", {})
            if not result:
                return None


            log_info = result.get("value", {})
            logs = log_info.get("logs", [])
            
            if logs:
                signature = log_info.get("signature")
                slot = log_info.get("slot", "Unknown")
                
                # Extract transaction details
                tx_details = {}
                for log in logs:
                    if "Transfer" in log:
                        parts = log.split()
                        if len(parts) >= 4:
                            tx_details["amount"] = parts[-1]
                            # Look for addresses in the log
                            for part in parts:
                                if len(part) > 30:  # Likely a Solana address
                                    if "from" not in tx_details:
                                        tx_details["from"] = part
                                    elif "to" not in tx_details:
                                        tx_details["to"] = part
                
                # Look for ray_log entries
                for log in logs:
                    if "ray_log:" in log:
                        ray_log_data = log.split("ray_log: ")[1]
                        decoded = decode_ray_log(ray_log_data)
                        
                        if decoded:
                            # Analyze for sandwich opportunity
                            opportunity = analyze_swap_opportunity(decoded)
                            
                            if opportunity:
                                self.total_opportunities += 1
                                is_profitable = opportunity['estimated_profit'] > 0
                                large_enough = opportunity['amount_in'] >= self.min_trade_size
                                
                                if is_profitable:
                                    self.successful_opportunities += 1
                                    logger.info("\n🚨 === POTENTIAL SANDWICH OPPORTUNITY === 🚨")
                                    logger.info(f"Transaction: {signature}")
                                    logger.info(f"Amount In: {opportunity['amount_in']/1e9:.4f} SOL")
                                    logger.info(f"Amount Out: {opportunity['amount_out']/1e9:.4f} SOL")
                                    logger.info(f"Price Impact: {opportunity['price_impact']*100:.2f}%")
                                    logger.info(f"Estimated Profit: {opportunity['estimated_profit']/1e9:.4f} SOL")
                                
                                # Execute or simulate trade
                                if is_profitable and large_enough and not self.dry_run:
                                    try:
                                        front_tx, back_tx = await self.builder.build_sandwich_transactions(opportunity)
                                        result = await self.executor.execute_sandwich(front_tx, back_tx)
                                        if result:
                                            self.total_profit += opportunity['estimated_profit']
                                            logger.info("Trade executed successfully!")
                                            logger.info(f"Front-run tx: https://explorer.solana.com/tx/{result['front_tx']}?cluster=devnet")
                                            logger.info(f"Back-run tx: https://explorer.solana.com/tx/{result['back_tx']}?cluster=devnet")
                                        else:
                                            logger.warning("Trade execution failed")
                                    except Exception as e:
                                        logger.error(f"Error executing trade: {e}")
                                else:
                                    timestamp = int(time.time())
                                    if self.dry_run:
                                        logger.info("Simulated transactions (not valid on Explorer):")
                                        logger.info(f"DRY_RUN_FRONT_{timestamp}")
                                        logger.info(f"DRY_RUN_BACK_{timestamp}")
                                    elif not large_enough:
                                        logger.info("Trade size too small for live execution")
                                        
                                logger.info("=========================================\n")
                                return
                
                # Print regular transaction info
                logger.debug("\n=== New Transaction Detected ===")
                logger.debug(f"Signature: {signature}")
                logger.debug(f"Slot: {slot}")
                if tx_details:
                    if "amount" in tx_details:
                        logger.debug(f"Amount: {tx_details['amount']} lamports")
                    if "from" in tx_details:
                        logger.debug(f"From: {tx_details['from']}")
                    if "to" in tx_details:
                        logger.debug(f"To: {tx_details['to']}")
                logger.debug("Program Logs:")
                for log in logs:
                    logger.debug(f"  {log}")
                logger.debug("============================\n")
                
                return {
                    "signature": signature,
                    "slot": slot,
                    "details": tx_details,
                    "logs": logs
                }
            
            return None
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing log: {e}")
            logger.debug(f"Problematic message: {log_msg}")
            return None
    
    async def monitor_swaps(self):
        """Main monitoring loop for swap opportunities."""
        try:
            async with websockets.connect(DEVNET_WS_URL) as websocket:
                subscription = await self.subscribe_to_program_logs()
                await websocket.send(json.dumps(subscription))
                
                # Wait for subscription confirmation
                response = await websocket.recv()
                subscription_response = json.loads(response)
                if "result" in subscription_response:
                    logger.info(f"Successfully subscribed to Raydium AMM program logs. Subscription ID: {subscription_response['result']}")
                else:
                    logger.warning(f"Unexpected subscription response: {subscription_response}")
                
                logger.info("Monitoring for Raydium AMM transactions...")
                
                # Initialize test mode variables
                last_activity = asyncio.get_event_loop().time()
                test_mode = False
                no_activity_timeout = 30  # seconds - shorter timeout for faster feedback
                
                # Log subscription attempt
                logger.info("Attempting to subscribe to Raydium AMM program logs...")
                
                while True:
                    try:
                        # Check for timeout and switch to test mode
                        current_time = asyncio.get_event_loop().time()
                        if not test_mode and current_time - last_activity > no_activity_timeout:
                            logger.info("No swap activity detected. Switching to test mode...")
                            test_mode = True
                            
                            # Simulate a swap event with complete ray_log data
                            current_time = int(time.time())
                            # Pack 7 u64 values: timestamp_in, amount_in, pool_id, timestamp_out, amount_out, pool_token, extra_data
                            ray_log_data = struct.pack('<QQQQQQQ',
                                current_time,           # timestamp_in
                                2_000_000_000,         # amount_in (2 SOL)
                                1,                     # pool_id (using 1 for SOL/USDC pool)
                                current_time + 1,      # timestamp_out
                                1_900_000_000,         # amount_out (1.9 SOL - simulating a profitable trade)
                                1,                     # pool_token
                                0                      # extra_data
                            )
                            
                            simulated_msg = {
                                "params": {
                                    "result": {
                                        "value": {
                                            "signature": f"simulated_swap_tx_{current_time}",
                                            "slot": current_time,
                                            "logs": [
                                                "Program log: Instruction: Swap",
                                                f"Program log: ray_log: {base64.b64encode(ray_log_data).decode('utf-8')}"
                                            ]
                                        }
                                    }
                                }
                            }
                            swap_info = await self.process_log(simulated_msg)
                            last_activity = current_time
                        else:
                            # Try to receive real message with timeout
                            try:
                                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                                log_msg = json.loads(message)
                                
                                # Skip heartbeat messages
                                if "method" in log_msg and log_msg["method"] == "logsNotification":
                                    last_activity = asyncio.get_event_loop().time()
                                    test_mode = False  # Reset test mode on real activity
                                    logger.debug("Received log notification")
                                
                                swap_info = await self.process_log(log_msg)
                            except asyncio.TimeoutError:
                                continue
                            
                        # For now, we're just monitoring transactions
                        if swap_info:
                            # Transaction info is already logged in process_log
                            pass
                            
                    except websockets.exceptions.ConnectionClosed:
                        logger.error("WebSocket connection closed. Retrying...")
                        break
                    except json.JSONDecodeError:
                        logger.error(f"Failed to decode message: {message}")
                        continue
                    except Exception as e:
                        logger.error(f"Error in monitoring loop: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise

async def main():
    """Entry point for the transaction monitor."""
    # Load existing keypair from wallet file
    payer = load_keypair()
    
    # Initialize client with rate limit handling
    client = AsyncClient(DEVNET_HTTP_URL)
    max_retries = 5
    initial_delay = 30
    max_delay = 300
    airdrop_amount = 2_000_000_000  # 2 SOL
    
    # Check current balance first
    try:
        logger.info("Checking current balance...")
        balance = await client.get_balance(payer.pubkey())
        if balance and balance.value >= 100_000_000:  # 0.1 SOL minimum
            logger.info(f"Sufficient balance found: {balance.value / 1_000_000_000:.3f} SOL")
            logger.info("Skipping airdrop process")
        else:
            # Only request airdrop if balance is insufficient
            logger.info(f"Insufficient balance: {balance.value if balance else 0} lamports")
            logger.info("Proceeding with airdrop process...")
    except Exception as e:
        if "429" in str(e):
            logger.warning("Rate limit reached during balance check. Waiting 5 seconds...")
            await asyncio.sleep(5)
        else:
            logger.error(f"Error checking balance: {e}")
            balance = None
    
    async def get_balance_with_backoff(client, pubkey, attempt):
        """Get balance with exponential backoff on rate limits."""
        try:
            return await client.get_balance(pubkey)
        except Exception as e:
            if "429" in str(e):
                delay = min(initial_delay * (2 ** attempt), max_delay)
                logger.warning(f"Rate limited on balance check. Waiting {delay} seconds.")
                await asyncio.sleep(delay)
                return None
            raise e
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Requesting airdrop (attempt {attempt + 1}/{max_retries})")
            
            # Check current balance first, with backoff
            balance = await get_balance_with_backoff(client, payer.pubkey(), attempt)
            if balance and balance.value > 0:
                logger.info(f"Already have balance: {balance.value} lamports")
                break
                
            # Calculate delay with exponential backoff
            if attempt > 0:
                delay = min(initial_delay * (2 ** (attempt - 1)), max_delay)
                logger.info(f"Implementing exponential backoff: waiting {delay} seconds before retry {attempt + 1}")
                await asyncio.sleep(delay)
            
            # Request airdrop with increased amount
            airdrop_sig = await client.request_airdrop(
                payer.pubkey(),
                airdrop_amount,
                commitment="confirmed"
            )
            
            if isinstance(airdrop_sig, str) or (hasattr(airdrop_sig, 'value') and airdrop_sig.value):
                logger.info(f"Airdrop requested: {airdrop_sig}")
                
                # Wait for confirmation with exponential backoff
                confirmation_attempts = 3  # Reduced confirmation checks
                for check in range(confirmation_attempts):
                    # Longer delays between balance checks
                    await asyncio.sleep(20 * (2 ** check))  # 20s, 40s, 80s
                    
                    
                    balance = await get_balance_with_backoff(client, payer.pubkey(), check)
                    if balance and balance.value > 0:
                        logger.info(f"Airdrop confirmed! Balance: {balance.value} lamports")
                        return  # Exit function on success
                
                logger.warning("Airdrop not confirmed after all attempts")
                continue
            else:
                logger.warning(f"Invalid airdrop response: {airdrop_sig}")
                
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:  # Rate limit error
                delay = min(initial_delay * (2 ** attempt), max_delay)
                logger.warning(f"Rate limited. Implementing exponential backoff: waiting {delay} seconds.")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Airdrop attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    delay = min(initial_delay * (2 ** attempt), max_delay)
                    logger.error(f"Waiting {delay} seconds before next attempt")
                    await asyncio.sleep(delay)
                else:
                    logger.error("All airdrop attempts failed. Running in test mode.")
    
    # Initialize monitor with rate limit awareness
    monitor = TransactionMonitor(payer)
    logger.info("Starting transaction monitor with rate limit handling (max 15 req/sec)")
    
    while True:
        try:
            # Check balance before monitoring to ensure we're still in live mode
            balance = await client.get_balance(payer.pubkey())
            if balance and balance.value >= 100_000_000:
                logger.info(f"Current balance: {balance.value / 1_000_000_000:.3f} SOL - Running in live mode")
            else:
                logger.warning(f"Low balance: {balance.value if balance else 0} lamports - Running in dry-run mode")
            
            # Add delay to respect rate limits
            await asyncio.sleep(1)  # Ensure we stay under 15 requests/sec
            await monitor.monitor_swaps()
            
        except Exception as e:
            if "429" in str(e):
                logger.warning("Rate limit reached. Waiting 5 seconds before retry.")
                await asyncio.sleep(5)
            else:
                logger.error(f"Monitor crashed: {e}")
                await asyncio.sleep(1)  # Wait before retrying

if __name__ == "__main__":
    asyncio.run(main())
