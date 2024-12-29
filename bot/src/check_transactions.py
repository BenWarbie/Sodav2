import logging
import time
from datetime import datetime

from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.signature import Signature

# Raydium AMM Program ID
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    # Use our Quicknode endpoint
    solana_client = Client(
        "https://few-cosmopolitan-borough.solana-devnet.quiknode.pro/"
        "1fe1f03ce011912127d3c733c5a61f0083ec910b"
    )
    wallet_address = "5RZNRgaqJzBBsfTWFNAws6pjb4s1nnjcEZaiANE8GxrD"
    pubkey = Pubkey.from_string(wallet_address)

    logger.info("Checking transactions for wallet: %s", wallet_address)
    explorer_url = "https://explorer.solana.com/address/%s" % wallet_address
    explorer_url += "?cluster=devnet"
    logger.info("Explorer URL: %s", explorer_url)

    try:
        # Get recent transactions
        response = solana_client.get_signatures_for_address(pubkey)

        if response.value:
            logger.info("\nRecent transactions:")
            for tx in response.value:
                sig = str(tx.signature)
                status = "✓" if tx.err is None else "✗"

                try:
                    # Get detailed transaction info
                    tx_info = solana_client.get_transaction(Signature.from_string(sig))
                    if tx_info.value:
                        block_time = tx_info.value.block_time
                        timestamp = (
                            datetime.fromtimestamp(block_time)
                            if block_time is not None
                            else "Unknown"
                        )
                        slot = tx_info.value.slot

                        # Check if transaction involves Raydium AMM
                        tx_data = tx_info.value
                        is_raydium = False
                        account_keys = []

                        try:
                            if hasattr(tx_data, "transaction") and hasattr(
                                tx_data.transaction, "message"
                            ):
                                account_keys = [
                                    str(key)
                                    for key in tx_data.transaction.message.account_keys
                                ]
                            elif hasattr(tx_data, "account_keys"):
                                account_keys = [
                                    str(key) for key in tx_data.account_keys
                                ]

                            is_raydium = RAYDIUM_AMM_V4 in account_keys
                            logger.debug("Found account keys: %s", account_keys)
                        except Exception as e:
                            logger.debug("Error processing account keys: %s", str(e))

                        logger.info("\n" + "=" * 50)
                        logger.info("Transaction Status: %s", status)
                        logger.info(
                            "Explorer Link: %s",
                            "https://explorer.solana.com/tx/%s" "?cluster=devnet" % sig,
                        )
                        logger.info("Timestamp: %s", timestamp)
                        logger.info("Slot: %d", slot)
                        logger.info(
                            "Involves Raydium AMM: %s", "Yes" if is_raydium else "No"
                        )

                        if is_raydium:
                            # Get transaction logs
                            if hasattr(tx_data, "meta") and hasattr(
                                tx_data.meta, "log_messages"
                            ):
                                logs = tx_data.meta.log_messages
                                if logs:
                                    logger.info("\nTransaction Logs:")
                                    for log in logs:
                                        if "Program log:" in log and "Raydium" in log:
                                            logger.info("  %s", log)
                        logger.info("=" * 50)
                except Exception as e:
                    logger.error("Error processing transaction %s: %s", sig, str(e))

                # Rate limit compliance - max 15 req/sec
                time.sleep(0.07)
        else:
            logger.info("No transactions found for this wallet on Devnet")

    except Exception as e:
        logger.error("Error checking transactions: %s", e)


if __name__ == "__main__":
    main()
