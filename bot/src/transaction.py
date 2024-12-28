"""Transaction builder for MEV sandwich bot."""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solders.hash import Hash
from solders.message import Message
import logging
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYS_PROGRAM_ID
from solders.transaction import VersionedTransaction

from .config import (
    DEVNET_HTTP_URL,
    RAYDIUM_AMM_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
)

logger = logging.getLogger(__name__)

@dataclass
class PoolDetails:
    """Details about a liquidity pool."""
    amm_id: Pubkey
    token_program: Pubkey
    token_a_account: Pubkey
    token_b_account: Pubkey
    pool_token_mint: Pubkey
    fee_account: Pubkey

class TransactionBuilder:
    """Builds sandwich attack transactions."""
    
    def __init__(
        self,
        client: AsyncClient,
        payer: Keypair,
        pool_details: PoolDetails
    ):
        """Initialize the transaction builder.
        
        Args:
            client: Solana RPC client
            payer: Keypair that will pay for transactions
            pool_details: Details about the liquidity pool to sandwich
        """
        self.client = client
        self.payer = payer
        self.pool = pool_details
        
    async def get_recent_blockhash(self) -> Hash:
        """Get a recent blockhash from the network.
        
        Returns:
            Hash: The recent blockhash as a Hash object
        """
        try:
            response = await self.client.get_latest_blockhash()
            # Return the blockhash directly
            return response.value.blockhash
        except Exception as e:
            logger.error(f"Failed to get recent blockhash: {e}")
            raise
            
    def create_swap_instruction(
        self,
        amount_in: int,
        minimum_amount_out: int,
        source_token: Pubkey,
        destination_token: Pubkey,
        authority: Pubkey
    ) -> Instruction:
        """Create a swap instruction for Raydium AMM.
        
        Args:
            amount_in: Amount of tokens to swap in
            minimum_amount_out: Minimum amount of tokens to receive
            source_token: Source token account
            destination_token: Destination token account
            authority: Account that owns the source tokens
        """
        # Create the swap instruction with proper account metas
        # This follows Raydium's swap instruction layout
        keys = [
            AccountMeta(pubkey=self.pool.amm_id, is_signer=False, is_writable=True),
            AccountMeta(pubkey=authority, is_signer=True, is_writable=False),
            AccountMeta(pubkey=source_token, is_signer=False, is_writable=True),
            AccountMeta(pubkey=destination_token, is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.pool.token_a_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.pool.token_b_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.pool.pool_token_mint, is_signer=False, is_writable=True),
            AccountMeta(pubkey=self.pool.fee_account, is_signer=False, is_writable=True),
            AccountMeta(pubkey=Pubkey.from_string(TOKEN_PROGRAM_ID), is_signer=False, is_writable=False),
        ]
        
        # Instruction data layout follows Raydium's format:
        # [u8 instruction, u64 amount_in, u64 minimum_amount_out]
        instruction_data = bytes([9])  # 9 = swap instruction in Raydium AMM
        instruction_data += amount_in.to_bytes(8, "little")
        instruction_data += minimum_amount_out.to_bytes(8, "little")
        
        return Instruction(
            program_id=Pubkey.from_string(RAYDIUM_AMM_PROGRAM_ID),
            accounts=keys,
            data=instruction_data
        )
        
    async def build_sandwich_transactions(
        self,
        front_run_amount: int,
        user_amount: int,
        back_run_amount: int,
        source_token: Pubkey,
        destination_token: Pubkey,
        minimum_output_amount: int
    ) -> Tuple[VersionedTransaction, VersionedTransaction]:
        """Build front-run and back-run transactions for a sandwich attack.
        
        Args:
            front_run_amount: Amount to swap in front-run
            user_amount: Amount user is swapping
            back_run_amount: Amount to swap in back-run
            source_token: Source token account
            destination_token: Destination token account
            minimum_output_amount: Minimum amount to receive
            
        Returns:
            Tuple of (front_run_transaction, back_run_transaction)
        """
        try:
            recent_blockhash = await self.get_recent_blockhash()
            
            # Create front-run transaction
            front_run_ix = self.create_swap_instruction(
                amount_in=front_run_amount,
                minimum_amount_out=minimum_output_amount,
                source_token=source_token,
                destination_token=destination_token,
                authority=self.payer.pubkey()
            )
            
            # Create front-run transaction message
            logging.info("Creating front-run transaction message...")
            front_message = Message.new_with_blockhash(
                [front_run_ix],
                self.payer.pubkey(),
                recent_blockhash
            )
            logging.info(f"Front-run message created: {front_message}")
            
            # Sign front-run message
            front_signature = self.payer.sign_message(front_message.serialize())
            logging.info(f"Front-run message signed: {front_signature.hex()}")
            
            # Create front-run transaction
            front_tx = VersionedTransaction(
                message=front_message,
                signatures=[front_signature]
            )
            logging.info(f"Created front-run transaction: {front_tx}")
            
            # Create back-run transaction
            logging.info("Creating back-run transaction...")
            back_run_ix = self.create_swap_instruction(
                amount_in=back_run_amount,
                minimum_amount_out=minimum_output_amount,
                source_token=destination_token,
                destination_token=source_token,
                authority=self.payer.pubkey()
            )
            
            # Create back-run transaction message
            back_message = Message.new_with_blockhash(
                [back_run_ix],
                self.payer.pubkey(),
                recent_blockhash
            )
            logging.info(f"Back-run message created: {back_message}")
            
            # Sign back-run message
            back_signature = self.payer.sign_message(back_message.serialize())
            logging.info(f"Back-run message signed: {back_signature.hex()}")
            
            # Create back-run transaction
            back_tx = VersionedTransaction(
                message=back_message,
                signatures=[back_signature]
            )
            logging.info(f"Created back-run transaction: {back_tx}")
            
            return front_tx, back_tx
            
        except Exception as e:
            logger.error(f"Failed to build sandwich transactions: {e}")
            raise
