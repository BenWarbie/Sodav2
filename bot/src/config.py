"""Configuration settings for the MEV bot."""

import logging
import os

from solders.keypair import Keypair

logger = logging.getLogger(__name__)

# Solana network configuration
DEVNET_WS_URL = (
    "wss://few-cosmopolitan-borough.solana-devnet.quiknode.pro/"
    "1fe1f03ce011912127d3c733c5a61f0083ec910b/"
)
DEVNET_HTTP_URL = (
    "https://few-cosmopolitan-borough.solana-devnet.quiknode.pro/"
    "1fe1f03ce011912127d3c733c5a61f0083ec910b/"
)

# Program IDs
RAYDIUM_AMM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Monitoring configuration
SUBSCRIPTION_ID = 1

# Wallet configuration
WALLET_PATH = os.path.expanduser("~/my-wallet.json")
MIN_BALANCE = 100_000_000  # 0.1 SOL minimum balance required for live mode


def load_keypair() -> Keypair:
    """Load the keypair from the wallet file.

    Returns:
        Keypair: The loaded Solana keypair

    Raises:
        FileNotFoundError: If wallet file doesn't exist
        ValueError: If wallet file format is invalid
    """
    if not os.path.exists(WALLET_PATH):
        raise FileNotFoundError(f"Wallet file not found at {WALLET_PATH}")

    try:
        # First try reading as JSON array
        import json

        with open(WALLET_PATH, "r") as f:
            try:
                keypair_data = json.load(f)
                if isinstance(keypair_data, list) and len(keypair_data) == 64:
                    return Keypair.from_bytes(bytes(keypair_data))
            except json.JSONDecodeError:
                pass

        # If not JSON, try reading as base58 private key
        with open(WALLET_PATH, "r") as f:
            private_key = f.read().strip()
            try:
                from base58 import b58decode

                keypair_bytes = b58decode(private_key)
                if len(keypair_bytes) == 64:
                    return Keypair.from_bytes(keypair_bytes)
            except Exception as e:
                logger.debug(f"Failed to decode base58: {e}")
                pass

        raise ValueError(
            "Invalid wallet file format. Expected JSON array or base58 private key"
        )

    except Exception as e:
        raise ValueError(f"Failed to load wallet: {str(e)}")
