# Add-ons (by Paul Parker)

This folder is the free **kraken-trades-motm** tool by Money on the Move
(original: https://github.com/moneyotm/kraken-trades-motm), unchanged, plus three add-on scripts.
Start with the original README.md and SETUP.md, then come back here.

| Script | What it does |
|---|---|
| `my_withdraw.py` | **Emergency withdraw** to your own hardware wallet. Sends only to the addresses in your `wallets.json`, and only if Kraken's saved address with the same name matches exactly. Dry run by default; a real send makes you type the last 6 characters of the address. |
| `my_swap.py` | Swap one coin for another (BTC, XRP, XLM, LUNA, USD). Uses a direct Kraken market when there is one, otherwise goes through USD. Real swaps make you type CONFIRM. |
| `my_reprice.py` | Change the price of an open limit order. |
| `open-terminal.ps1` | Windows launcher: a terminal where you just type `swap`, `withdraw`, `reprice`, `balance` or `orders`. |

## Extra setup for the add-ons (Windows)
1. Do the original SETUP.md first (Python, the `.venv` folder, your Kraken API key in `.env`).
2. Install one extra library: `.venv\Scripts\python.exe -m pip install krakenex`
3. Copy `wallets.example.json` to `wallets.json` and put in **your own** receive addresses.
4. On Kraken, save the same addresses in Funding > Withdraw (address book) **with the same names** as in `wallets.json`, and confirm Kraken's emails.
5. Your Kraken API key needs **Withdraw funds** for `my_withdraw.py`, plus the trading permissions for swap/reprice.
6. Right-click `open-terminal.ps1` > Run with PowerShell.

## Safety
- Everything is a dry run unless you do the live/send step and confirm it.
- `wallets.json`, `.env` and `logs/` are in `.gitignore`. Never share them.
- Use at your own risk. Not financial advice.
