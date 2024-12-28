# Solana MEV Sandwich Bot

A Python-based MEV (Miner Extractable Value) bot for detecting and executing sandwich attacks on Raydium AMM swaps in the Solana Devnet environment.

## Features

- Real-time monitoring of Raydium AMM transactions
- Automatic sandwich opportunity detection
- Rate-limited RPC calls (15 req/sec for Quicknode free tier)
- Transaction verification with detailed logging
- Support for both live and dry-run modes

## Prerequisites

- Python 3.12
- Solana CLI tools (v1.17 or later)
- Quicknode RPC endpoint (Devnet)
- Solana wallet with sufficient SOL for transactions

### Installing Solana CLI
1. Install Solana CLI tools:
```bash
sh -c "$(curl -sSfL https://release.solana.com/v1.17.0/install)"
```

2. Update PATH (add to ~/.bashrc):
```bash
export PATH="/root/.local/share/solana/install/active_release/bin:$PATH"
```

3. Verify installation:
```bash
solana --version
```

## Installation

1. Ensure Python 3.12 is installed:
```bash
python --version  # Should show Python 3.12.x
```
If not installed, use pyenv:
```bash
pyenv install 3.12
pyenv global 3.12
```

2. Clone the repository:
```bash
git clone https://github.com/BenWarbie/Sodav2.git
cd Sodav2
```

3. Create virtual environment:
```bash
python -m venv mev-bot-env
```

4. Setup and install dependencies:
```bash
# Change to project directory and update repository
cd ~/repos/Sodav2 && git pull --rebase

# Activate virtual environment and install dependencies
source mev-bot-env/bin/activate && pip install -r requirements.txt
```

5. Verify installation:
```bash
# Verify solana-py installation
python -c "import solana; print(f'solana-py version: {solana.__version__}')"

# Verify environment activation
echo $VIRTUAL_ENV  # Should show path to mev-bot-env
```

## Configuration

1. Solana Wallet Setup:
   - Generate a new wallet:
     ```bash
     solana-keygen new --outfile ~/my-wallet.json
     ```
   - Or use existing wallet file in JSON array or base58 format
   - Fund your wallet using Solana Devnet Faucet:
     - Option 1: Use solfaucet.com (recommended)
     - Option 2: CLI command:
       ```bash
       solana airdrop 2 $(solana-keygen pubkey ~/my-wallet.json) --url devnet
       ```
   - Ensure minimum balance of 0.1 SOL for live mode
   - Verify balance:
     ```bash
     solana balance $(solana-keygen pubkey ~/my-wallet.json) --url devnet
     ```

2. Quicknode RPC Configuration:
   - HTTP Endpoint: Configure in `bot/src/config.py`
   - WebSocket Endpoint: Configure in `bot/src/config.py`
   - Default Rate Limit: 15 requests per second

## Usage

1. Start the transaction monitor:
```bash
cd bot
python -m src.monitor
```

2. Monitor output for sandwich opportunities:
- The bot will display detected Raydium AMM transactions
- Potential sandwich opportunities will be highlighted
- Transaction details and pool information will be logged

## Project Structure

```
bot/
├── src/
│   ├── __init__.py
│   ├── config.py          # Configuration settings
│   ├── monitor.py         # Main transaction monitor
│   ├── executor.py        # Transaction execution
│   ├── transaction.py     # Transaction building
│   ├── rate_limiter.py    # Rate limiting for RPC
│   ├── ray_log_decoder.py # Raydium log parsing
│   └── check_transactions.py
├── tests/
│   ├── __init__.py
│   └── test_ray_log_decoder.py
└── requirements.txt
```

## Development

1. Install development dependencies:
```bash
pip install black isort flake8
```

2. Run tests:
```bash
pytest
```

3. Format code:
```bash
black .
isort .
```

4. Run linter:
```bash
flake8
```

## Important Notes

- Currently configured for Devnet testing
- Rate limited to comply with Quicknode free tier (15 req/sec)
- Includes comprehensive error handling and logging
- Monitor RPC usage to avoid exceeding rate limits

## License

[Add License Information]

## Contributing

[Add Contributing Guidelines]
