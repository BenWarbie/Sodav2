"""Transaction monitor for detecting swap opportunities on Solana."""

import asyncio
import base64
import json
import logging
import struct
import time
from collections import deque
from datetime import datetime
from typing import Dict, Optional

import websockets
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .config import (DEVNET_HTTP_URL, DEVNET_WS_URL, RAYDIUM_AMM_PROGRAM_ID,
                     SUBSCRIPTION_ID, TOKEN_PROGRAM_ID, load_keypair)
from .executor import TransactionExecutor
from .ray_log_decoder import decode_ray_log
from .simulation import simulate_sandwich
from .transaction import PoolDetails, TransactionBuilder

# Constants
SYSTEM_PROGRAM = "11111111111111111111111111111111"
MAX_REQUESTS_PER_SECOND = 15
REQUEST_WINDOW = 1  # seconds

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
# Set websockets logger to WARNING to reduce noise
logging.getLogger("websockets").setLevel(logging.WARNING)
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
        self.pool_reserves_cache = {}  # Cache pool reserves to reduce RPC calls
        self.last_pool_update = 0  # Timestamp of last pool reserves update
        self.subscription_active = False
        self.last_connection_attempt = 0
        self.connection_retry_delay = 5  # Start with 5 second delay
        self.pool_details = PoolDetails(
            amm_id=Pubkey.from_string(RAYDIUM_AMM_PROGRAM_ID),
            token_program=Pubkey.from_string(TOKEN_PROGRAM_ID),
            # Using SOL-USDC pool for testing
            token_a_account=Pubkey.from_string(
                "9wFFyRfZBsuAha4YcuxcXLKwMxJR43S7fPfQLusDBzvT"  # SOL
            ),
            token_b_account=Pubkey.from_string(
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
            ),
            pool_token_mint=Pubkey.from_string(
                "8HoQnePLqPj4M7PUDzfw8e3Ymdwgc7NLGnaTUapubyvu"
            ),
            fee_account=Pubkey.from_string(
                "4Zc4kQZhRQeGztihvcGSWezJE1k44kKEgPCAkdeBfras"
            ),
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

    async def update_pool_reserves(self, pool_type: str) -> None:
        """Update pool reserves for the given pool type.
        
        Args:
            pool_type: Pool identifier (e.g., "SOL/USDC")
        """
        try:
            # Get pool account info
            pool_account = self.pool_details.amm_id
            response = await self.client.get_account_info(pool_account)
            
            if response and response.value:
                data = response.value.data
                # Parse pool data (simplified for now)
                token_a_reserve = int.from_bytes(data[64:72], byteorder='little')
                token_b_reserve = int.from_bytes(data[72:80], byteorder='little')
                
                self.pool_reserves_cache[pool_type] = {
                    "token_a": token_a_reserve,
                    "token_b": token_b_reserve,
                    "last_update": time.time()
                }
                logger.info("Updated pool reserves for %s - A: %d, B: %d", 
                          pool_type, token_a_reserve, token_b_reserve)
            else:
                logger.error("Failed to fetch pool account data")
                
        except Exception as e:
            logger.error("Error updating pool reserves: %s", e)
            
    async def subscribe_to_program_logs(self) -> Dict:
        """Create a subscription request for program logs."""
        if not self.check_rate_limit():
            logger.warning("Rate limit reached, delaying subscription")
            await asyncio.sleep(1)
            return None

        logger.info("Setting up subscriptions for System Program and Raydium AMM")
        logger.info("System Program: %s", SYSTEM_PROGRAM)
        logger.info("Raydium AMM: %s", RAYDIUM_AMM_PROGRAM_ID)

        return {
            "jsonrpc": "2.0",
            "id": self.subscription_id,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [RAYDIUM_AMM_PROGRAM_ID]},
                {"commitment": "confirmed"},
            ],
        }

    def check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()

        # Remove timestamps older than our window
        while (
            self.request_timestamps
            and now - self.request_timestamps[0] > REQUEST_WINDOW
        ):
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
            logger.info("Monitor Stats:")
            logger.info("  Requests/second: %.2f", requests_per_second)
            logger.info("  Total requests: %d", self.total_requests)
            logger.info("  Rate limit hits: %d", self.rate_limit_hits)
            logger.info("  Total opportunities: %d", self.total_opportunities)
            logger.info("  Uptime: %s", uptime)
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
                sub_id = log_msg["result"]
                logger.info(
                    "WebSocket connection established - Subscription ID: %s", sub_id
                )
                logger.info(
                    "Monitoring System Program and Raydium AMM transactions on Solana Devnet"
                )
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

                # Print raw logs for debugging
            logger.info("Transaction logs for %s:", signature)
            for log in logs:
                logger.info("  Raw log: %s", log)

            # Look for Raydium AMM program logs
            raydium_logs = [log for log in logs if "Program " + RAYDIUM_AMM_PROGRAM_ID in log]
            if raydium_logs:
                logger.info("Found Raydium AMM logs (%d):", len(raydium_logs))
                for log in raydium_logs:
                    logger.info("  Raydium log: %s", log)
                    
                # Check for specific Raydium instruction patterns
                swap_instructions = [log for log in raydium_logs if any(pattern in log for pattern in [
                    "Instruction: Swap",
                    "ray_log:",
                    "Program data: ",
                ])]
                if swap_instructions:
                    logger.info("Found potential swap instructions:")
                    for instruction in swap_instructions:
                        logger.info("  Swap instruction: %s", instruction)

            # Look for ray_log entries
            for log in logs:
                if "ray_log:" in log:
                    logger.info("=== Processing ray_log entry ===")
                    logger.info("Raw log: %s", log)
                    ray_log_data = log.split("ray_log: ")[1]
                    logger.info("Extracted ray_log data: %s", ray_log_data)
                    
                    try:
                        decoded = decode_ray_log(ray_log_data)
                        logger.info("Decoded ray_log data: %s", decoded)
                    except Exception as e:
                        logger.error("Failed to decode ray_log: %s", e)
                        continue

                    if decoded:
                        # Validate decoded data against expected format
                        logger.info("Decoded ray_log data:")
                        amount_in = decoded.get("amount_in", 0)
                        amount_out = decoded.get("amount_out", 0)
                        pool_type = decoded.get("pool_type", "unknown")
                        pool_id = decoded.get("pool_id", "unknown")
                        
                        # Update pool reserves if needed
                        now = time.time()
                        if now - self.last_pool_update > 60:  # Update every 60 seconds
                            await self.update_pool_reserves(pool_type)
                            self.last_pool_update = now
                        
                        logger.info("=== Validated Transaction Details ===")
                        logger.info("Transaction Signature: %s", signature)
                        logger.info("Slot: %s", slot)
                        logger.info("Amount In: %d lamports (%.4f SOL)", amount_in, amount_in / 1e9)
                        logger.info("Amount Out: %d lamports (%.4f SOL)", amount_out, amount_out / 1e9)
                        logger.info("Pool Type: %s", pool_type)
                        logger.info("Pool ID: %s", pool_id)
                        logger.info("Explorer URL: https://explorer.solana.com/tx/%s?cluster=devnet", signature)
                        
                        # Validate data consistency
                        if amount_in <= 0 or amount_out <= 0:
                            logger.warning("Invalid amounts detected - skipping opportunity")
                            continue
                            
                        if pool_type not in ["SOL/USDC", "SOL/USDT"]:
                            logger.warning("Unsupported pool type: %s - skipping", pool_type)
                            continue
                        
                        # Get pool reserves and calculate slippage
                        pool_reserves = self.pool_reserves_cache.get(pool_type)
                        if pool_reserves and time.time() - pool_reserves.get("last_update", 0) < 300:  # Valid for 5 minutes
                            token_a_reserve = pool_reserves.get("token_a", 0)
                            token_b_reserve = pool_reserves.get("token_b", 0)
                            logger.info("Pool reserves - Token A: %d, Token B: %d", 
                                      token_a_reserve, token_b_reserve)
                            
                            # Calculate price impact and max slippage
                            price_impact = ((amount_out / amount_in) - 1) * 100 if amount_in > 0 else 0
                            max_slippage = min(amount_in / token_a_reserve * 100, 2.0)  # Cap at 2%
                            logger.info("  Price Impact: %.2f%%, Max Slippage: %.2f%%", 
                                      price_impact, max_slippage)
                        
                        # Validate amounts, pool type, and slippage
                        if (amount_in > 0 and amount_out > 0 and
                            pool_type in ["SOL/USDC", "SOL/USDT"] and
                            abs(price_impact) >= 0.01 and  # Min 0.01% impact
                            abs(price_impact) <= max_slippage):  # Respect slippage
                            logger.info("Valid swap detected with significant price impact")
                            # Simulate sandwich opportunity
                            simulation = simulate_sandwich(
                                decoded, self.pool_details, dry_run=self.dry_run
                            )

                            if simulation:
                                self.total_opportunities += 1
                                is_profitable = simulation.is_profitable
                                large_enough = (
                                    decoded["amount_in"] >= self.min_trade_size
                                )

                                if is_profitable:
                                    self.successful_opportunities += 1
                                    # Format amounts in SOL
                                    amounts = {
                                        "in": decoded["amount_in"] / 1e9,
                                        "out": decoded["amount_out"] / 1e9,
                                        "front": simulation.front_run_profit / 1e9,
                                        "back": simulation.back_run_profit / 1e9,
                                        "gas": simulation.gas_cost / 1e9,
                                        "fees": simulation.pool_fees / 1e9,
                                        "net": simulation.net_profit / 1e9,
                                    }

                                    logger.info(
                                        "\n🚨 === POTENTIAL SANDWICH OPPORTUNITY === 🚨"
                                    )
                                    logger.info("Transaction: %s", signature)
                                    logger.info("Amount In: %.4f SOL", amounts["in"])
                                    logger.info("Amount Out: %.4f SOL", amounts["out"])
                                    logger.info(
                                        "Front-run Profit: %.4f SOL", amounts["front"]
                                    )
                                    logger.info(
                                        "Back-run Profit: %.4f SOL", amounts["back"]
                                    )
                                    logger.info("Gas Cost: %.4f SOL", amounts["gas"])
                                    logger.info("Pool Fees: %.4f SOL", amounts["fees"])
                                    logger.info("Net Profit: %.4f SOL", amounts["net"])

                                # Execute or simulate trade
                                if is_profitable and large_enough and not self.dry_run:
                                    try:
                                        # Calculate front-run and back-run amounts (25% of detected trade)
                                        front_run_amount = decoded["amount_in"] // 4
                                        back_run_amount = front_run_amount

                                        # Build sandwich transactions with 2% slippage
                                        tx_params = {
                                            "front_run_amount": front_run_amount,
                                            "user_amount": decoded["amount_in"],
                                            "back_run_amount": back_run_amount,
                                            "source_token": (
                                                self.pool_details.token_a_account
                                            ),
                                            "destination_token": (
                                                self.pool_details.token_b_account
                                            ),
                                            "minimum_output_amount": int(
                                                decoded["amount_out"] * 0.98
                                            ),
                                        }

                                        front_tx, back_tx = (
                                            await self.builder.build_sandwich_transactions(
                                                **tx_params
                                            )
                                        )
                                        result = await self.executor.execute_sandwich(
                                            front_tx, back_tx
                                        )
                                        if result:
                                            self.total_profit += simulation.net_profit
                                            logger.info("Trade executed successfully!")
                                            base_url = "https://explorer.solana.com/tx"
                                            logger.info(
                                                "Front-run tx: %s/%s?cluster=devnet",
                                                base_url,
                                                result["front_tx"],
                                            )
                                            logger.info(
                                                "Back-run tx: %s/%s?cluster=devnet",
                                                base_url,
                                                result["back_tx"],
                                            )
                                        else:
                                            logger.warning("Trade execution failed")
                                    except Exception as e:
                                        logger.error("Error executing trade: %s", e)
                                else:
                                    timestamp = int(time.time())
                                    if self.dry_run:
                                        logger.info(
                                            "Simulated transactions (not valid on Explorer):"
                                        )
                                        logger.info("DRY_RUN_FRONT_%d", timestamp)
                                        logger.info("DRY_RUN_BACK_%d", timestamp)
                                    elif not large_enough:
                                        logger.info(
                                            "Trade size too small for live execution"
                                        )

                                logger.info(
                                    "=========================================\n"
                                )
                                return

                # Print regular transaction info
                logger.debug("\n=== New Transaction Detected ===")
                logger.debug("Signature: %s", signature)
                logger.debug("Slot: %s", slot)
                if tx_details:
                    if "amount" in tx_details:
                        logger.debug("Amount: %s lamports", tx_details["amount"])
                    if "from" in tx_details:
                        logger.debug("From: %s", tx_details["from"])
                    if "to" in tx_details:
                        logger.debug("To: %s", tx_details["to"])
                logger.debug("Program Logs:")
                for log in logs:
                    logger.debug("  %s", log)
                logger.debug("============================\n")

                return {
                    "signature": signature,
                    "slot": slot,
                    "details": tx_details,
                    "logs": logs,
                }

            return None

        except json.JSONDecodeError as e:
            logger.error("Failed to decode JSON message: %s", e)
            return None
        except Exception as e:
            logger.error("Error processing log: %s", e)
            logger.debug("Problematic message: %s", log_msg)
            return None

    async def monitor_swaps(self):
        """Main monitoring loop for swap opportunities."""
        while True:
            try:
                # Check if we should attempt reconnection
                now = time.time()
                if not self.subscription_active and (now - self.last_connection_attempt) >= self.connection_retry_delay:
                    self.last_connection_attempt = now
                    logger.info("Attempting to establish WebSocket connection...")
                    
                    try:
                        async with websockets.connect(DEVNET_WS_URL) as websocket:
                            # Reset retry delay on successful connection
                            self.connection_retry_delay = 5
                            
                            # Subscribe to program logs
                            subscription = await self.subscribe_to_program_logs()
                            if not subscription:
                                logger.error("Failed to create subscription request")
                                raise Exception("Subscription request failed")
                                
                            await websocket.send(json.dumps(subscription))

                            # Wait for subscription confirmation
                            response = await websocket.recv()
                            subscription_response = json.loads(response)
                            if "result" in subscription_response:
                                self.subscription_active = True
                                logger.info(
                                    "Successfully subscribed to Raydium AMM program logs. "
                                    "Subscription ID: %s",
                                    subscription_response["result"],
                                )
                            else:
                                logger.warning(
                                    "Unexpected subscription response: %s", subscription_response
                                )
                                raise Exception("Invalid subscription response")

                            # Main message processing loop
                            while True:
                                try:
                                    message = await websocket.recv()
                                    await self.process_log(message)
                                except websockets.exceptions.ConnectionClosed:
                                    logger.warning("WebSocket connection closed")
                                    self.subscription_active = False
                                    break
                                except Exception as e:
                                    logger.error("Error processing message: %s", e)
                                    continue

                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("WebSocket connection closed")
                        self.subscription_active = False
                        self.connection_retry_delay = min(self.connection_retry_delay * 2, 60)
                    except Exception as e:
                        logger.error("WebSocket connection error: %s", e)
                        self.subscription_active = False
                        self.connection_retry_delay = min(self.connection_retry_delay * 2, 60)

                await asyncio.sleep(1)  # Prevent tight loop when not connected

            except Exception as e:
                logger.error("Critical error in monitoring loop: %s", e)
                self.subscription_active = False
                await asyncio.sleep(5)  # Wait before retrying after critical error

                logger.info("Monitoring for Raydium AMM transactions...")

                # Initialize test mode variables
                last_activity = asyncio.get_event_loop().time()
                test_mode = False
                no_activity_timeout = (
                    30  # seconds - shorter timeout for faster feedback
                )

                # Log subscription attempt
                logger.info("Attempting to subscribe to Raydium AMM program logs...")

                while True:
                    try:
                        # Check for timeout and switch to test mode
                        current_time = asyncio.get_event_loop().time()
                        if (
                            not test_mode
                            and current_time - last_activity > no_activity_timeout
                        ):
                            logger.info(
                                "No swap activity detected. Switching to test mode..."
                            )
                            test_mode = True

                            # Simulate a swap event with complete ray_log data
                            current_time = int(time.time())
                            # Pack 7 u64 values: timestamp_in, amount_in, pool_id,
                            # timestamp_out, amount_out, pool_token, extra_data
                            # Pack test transaction data
                            test_data = {
                                "timestamp_in": current_time,
                                "amount_in": 2_000_000_000,  # 2 SOL
                                "pool_id": 1,  # SOL/USDC pool
                                "timestamp_out": current_time + 1,
                                "amount_out": 1_900_000_000,  # 1.9 SOL
                                "pool_token": 1,
                                "extra_data": 0,
                            }
                            ray_log_data = struct.pack(
                                "<QQQQQQQ",
                                test_data["timestamp_in"],
                                test_data["amount_in"],
                                test_data["pool_id"],
                                test_data["timestamp_out"],
                                test_data["amount_out"],
                                test_data["pool_token"],
                                test_data["extra_data"],
                            )

                            simulated_msg = {
                                "params": {
                                    "result": {
                                        "value": {
                                            "signature": "simulated_swap_tx_%d"
                                            % current_time,
                                            "slot": current_time,
                                            "logs": [
                                                "Program log: Instruction: Swap",
                                                "Program log: ray_log: %s"
                                                % base64.b64encode(ray_log_data).decode(
                                                    "utf-8"
                                                ),
                                            ],
                                        }
                                    }
                                }
                            }
                            swap_info = await self.process_log(simulated_msg)
                            last_activity = current_time
                        else:
                            # Try to receive real message with timeout
                            try:
                                message = await asyncio.wait_for(
                                    websocket.recv(), timeout=5.0
                                )
                                log_msg = json.loads(message)

                                # Skip heartbeat messages
                                if (
                                    "method" in log_msg
                                    and log_msg["method"] == "logsNotification"
                                ):
                                    last_activity = asyncio.get_event_loop().time()
                                    test_mode = (
                                        False  # Reset test mode on real activity
                                    )
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
                        logger.error("Failed to decode message: %s", message)
                        continue
                    except Exception as e:
                        logger.error("Error in monitoring loop: %s", e)
                        continue

        except Exception as e:
            logger.error("Failed to connect to WebSocket: %s", e)
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
            balance_sol = balance.value / 1_000_000_000
            logger.info("Sufficient balance found: %.3f SOL", balance_sol)
            logger.info("Skipping airdrop process")
        else:
            # Only request airdrop if balance is insufficient
            balance_lamports = balance.value if balance else 0
            logger.info("Insufficient balance: %d lamports", balance_lamports)
            logger.info("Proceeding with airdrop process...")
    except Exception as e:
        if "429" in str(e):
            logger.warning(
                "Rate limit reached during balance check. Waiting 5 seconds..."
            )
            await asyncio.sleep(5)
        else:
            logger.error("Error checking balance: %s", e)
            balance = None

    async def get_balance_with_backoff(client, pubkey, attempt):
        """Get balance with exponential backoff on rate limits."""
        try:
            return await client.get_balance(pubkey)
        except Exception as e:
            if "429" in str(e):
                delay = min(initial_delay * (2**attempt), max_delay)
                logger.warning(
                    "Rate limited on balance check. Waiting %d seconds." % delay
                )
                await asyncio.sleep(delay)
                return None
            raise e

    for attempt in range(max_retries):
        try:
            logger.info("Requesting airdrop (attempt %d/%d)", attempt + 1, max_retries)

            # Check current balance first, with backoff
            balance = await get_balance_with_backoff(client, payer.pubkey(), attempt)
            if balance and balance.value > 0:
                logger.info("Already have balance: %d lamports", balance.value)
                break

            # Calculate delay with exponential backoff
            if attempt > 0:
                delay = min(initial_delay * (2 ** (attempt - 1)), max_delay)
                logger.info(
                    "Implementing exponential backoff: waiting %d seconds "
                    "before retry %d" % (delay, attempt + 1)
                )
                await asyncio.sleep(delay)

            # Request airdrop with increased amount
            airdrop_sig = await client.request_airdrop(
                payer.pubkey(), airdrop_amount, commitment="confirmed"
            )

            if isinstance(airdrop_sig, str) or (
                hasattr(airdrop_sig, "value") and airdrop_sig.value
            ):
                logger.info("Airdrop requested: %s", airdrop_sig)

                # Wait for confirmation with exponential backoff
                confirmation_attempts = 3  # Reduced confirmation checks
                for check in range(confirmation_attempts):
                    # Longer delays between balance checks
                    await asyncio.sleep(20 * (2**check))  # 20s, 40s, 80s

                    balance = await get_balance_with_backoff(
                        client, payer.pubkey(), check
                    )
                    if balance and balance.value > 0:
                        logger.info(
                            "Airdrop confirmed! Balance: %d lamports", balance.value
                        )
                        return  # Exit function on success

                logger.warning("Airdrop not confirmed after all attempts")
                continue
            else:
                logger.warning("Invalid airdrop response: %s", airdrop_sig)

        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:  # Rate limit error
                delay = min(initial_delay * (2**attempt), max_delay)
                logger.warning(
                    "Rate limited. Implementing exponential backoff: "
                    "waiting %d seconds." % delay
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Airdrop attempt %d failed: %s", attempt + 1, e)
                if attempt < max_retries - 1:
                    delay = min(initial_delay * (2**attempt), max_delay)
                    logger.error("Waiting %d seconds before next attempt", delay)
                    await asyncio.sleep(delay)
                else:
                    logger.error("All airdrop attempts failed. Running in test mode.")

    # Initialize monitor with rate limit awareness
    monitor = TransactionMonitor(payer)
    logger.info(
        "Starting transaction monitor with rate limit handling (max 15 req/sec)"
    )

    while True:
        try:
            # Check balance before monitoring to ensure we're still in live mode
            balance = await client.get_balance(payer.pubkey())
            if balance and balance.value >= 100_000_000:
                balance_sol = balance.value / 1_000_000_000
                logger.info(
                    "Current balance: %.3f SOL - Running in live mode", balance_sol
                )
            else:
                balance_lamports = balance.value if balance else 0
                logger.warning(
                    "Low balance: %d lamports - Running in dry-run mode",
                    balance_lamports,
                )

            # Add delay to respect rate limits
            await asyncio.sleep(1)  # Ensure we stay under 15 requests/sec
            await monitor.monitor_swaps()

        except Exception as e:
            if "429" in str(e):
                logger.warning("Rate limit reached. Waiting 5 seconds before retry.")
                await asyncio.sleep(5)
            else:
                logger.error("Monitor crashed: %s", e)
                await asyncio.sleep(1)  # Wait before retrying


if __name__ == "__main__":
    asyncio.run(main())
