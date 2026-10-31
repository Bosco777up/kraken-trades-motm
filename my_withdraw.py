"""my_withdraw.py - Emergency withdraw tool (Kraken Funding v1 API).

Sends ONLY to the wallets in wallets.json, and only if Kraken's saved address
with that name matches the built-in address exactly. Dry-run ("quote") by
default. "send" is for you to run yourself: it asks you to type the last 6
characters of the address. An AI assistant should never run "send" for you.

Usage (in this folder):
  python my_withdraw.py probe                 # read-only: methods + saved addresses
  python my_withdraw.py quote XRP 20          # read-only: fee, net amount, address
  python my_withdraw.py send  XRP 20          # REAL withdrawal - you run this yourself
"""
import base64, hashlib, hmac, json, sys, time, urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from dotenv import dotenv_values

DIR = Path(__file__).resolve().parent
BASE = "https://api.kraken.com"
LOG = DIR / "logs" / "my_withdraw_log.jsonl"

# Your wallets live in wallets.json next to this script (copy wallets.example.json).
# name = the exact name you saved on Kraken (Funding > Withdraw > address book).
# method = Kraken network name (Bitcoin, Ripple, Stellar). address = your full receive address.
WALLETS_FILE = DIR / "wallets.json"
if not WALLETS_FILE.exists():
    print("\nwallets.json not found. Copy wallets.example.json to wallets.json and fill in your own addresses.")
    sys.exit(1)
WALLETS = {k: v for k, v in json.loads(WALLETS_FILE.read_text(encoding="utf-8")).items() if not k.startswith("_")}
# Kraken asset names to try, in order, for each wallet.
ASSET_NAMES = {"BTC": ["BTC", "XBT"], "XRP": ["XRP"], "XLM": ["XLM"]}


def abort(msg):
    print(f"\nABORTED: {msg}")
    sys.exit(1)


def _encode(params, prefix=None, out=None):
    """Nested query encoding per Kraken's funding guide: asset[class]=currency."""
    out = [] if out is None else out
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else k
        if v is None:
            continue
        if isinstance(v, dict):
            _encode(v, key, out)
        elif isinstance(v, bool):
            out.append((key, "true" if v else "false"))
        else:
            out.append((key, str(v)))
    return out


_last_nonce = [0]


def call(method, path, query=None, body=None):
    """Signed request to a Kraken funding endpoint. Keys are read from .env
    and only used to sign; they are never printed."""
    cfg = dotenv_values(DIR / ".env")
    key, secret = cfg.get("KRAKEN_API_KEY"), cfg.get("KRAKEN_API_SECRET")
    if not key or not secret or "paste_your" in key:
        abort("KRAKEN_API_KEY / KRAKEN_API_SECRET missing in .env")
    nonce = max(int(time.time() * 1000), _last_nonce[0] + 1)
    _last_nonce[0] = nonce
    nonce = str(nonce)
    qs = urllib.parse.urlencode(_encode(query or {}))
    signed_path = path + (f"?{qs}" if qs else "")
    body_s = json.dumps(body, separators=(",", ":")) if body is not None else ""
    digest = hashlib.sha256((nonce + body_s).encode()).digest()
    sig = base64.b64encode(hmac.new(base64.b64decode(secret),
                                    signed_path.encode() + digest,
                                    hashlib.sha512).digest()).decode()
    headers = {"api-key": key, "api-sign": sig, "api-nonce": nonce}
    if body is not None:
        headers["content-type"] = "application/json"
    r = requests.request(method, BASE + signed_path, headers=headers,
                         data=body_s if body is not None else None, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:500]}
    if r.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
        abort(f"Kraken {method} {path} -> HTTP {r.status_code}: {data}")
    return data


def withdraw_methods(coin):
    """Return (asset_name, methods list) for a coin, trying Kraken's names."""
    last = None
    for name in ASSET_NAMES[coin]:
        try:
            data = call("GET", "/funding/v1/methods/withdraw",
                        {"asset": {"class": "currency", "name": name}, "limit": 50})
        except SystemExit:
            last = name
            continue
        methods = data.get("methods") or []
        if methods:
            return name, methods
    abort(f"No withdraw methods found for {coin} (tried {ASSET_NAMES[coin]})")


def saved_addresses(method_id):
    data = call("GET", "/funding/v1/addresses",
                {"scope": {"method_id": method_id}, "limit": 50})
    return data.get("addresses") or []


def _addr(a):
    return ((a.get("address_details") or {}).get("crypto") or {}).get("address", "")


def cmd_probe():
    """Read-only. Show methods and saved addresses for each built-in coin."""
    for coin, w in WALLETS.items():
        asset, methods = withdraw_methods(coin)
        print(f"\n== {coin} (Kraken asset '{asset}')")
        for m in methods:
            net = (m.get("network") or {}).get("network_name", "")
            mark = "  <-- used" if (m.get("network") or {}).get("network_name") == w["method"] else ""
            print(f"   method: {m.get('method_name')}  network: {net}  "
                  f"min: {m.get('minimum_amount')}{mark}")
        m = next((m for m in methods if (m.get("network") or {}).get("network_name") == w["method"]), None)
        if not m:
            print(f"   !! method '{w['method']}' not found - script needs updating")
            continue
        addrs = saved_addresses(m["method_id"])
        if not addrs:
            print("   saved addresses: none")
        for a in addrs:
            ok = "yes" if a.get("verified") else "NO"
            print(f"   saved: '{a.get('name')}'  verified: {ok}  address: {_addr(a)}")


def _resolve(coin, amount_str):
    if coin not in WALLETS:
        abort(f"Unknown coin '{coin}'. In wallets.json: {', '.join(WALLETS)}")
    w = WALLETS[coin]
    if not w["address"]:
        abort(f"No address built in for {coin} yet")
    try:
        amount = Decimal(amount_str)
    except InvalidOperation:
        abort(f"Amount '{amount_str}' is not a number")
    if amount <= 0:
        abort("Amount must be positive")
    asset, methods = withdraw_methods(coin)
    m = next((m for m in methods if (m.get("network") or {}).get("network_name") == w["method"]), None)
    if not m:
        abort(f"Kraken method '{w['method']}' not found for {coin}")
    minimum = m.get("minimum_amount")
    if minimum and amount < Decimal(str(minimum)):
        abort(f"{amount} {coin} is below Kraken's minimum of {minimum}")
    matches = [a for a in saved_addresses(m["method_id"]) if a.get("name") == w["name"]]
    if len(matches) != 1:
        abort(f"Expected exactly one saved address named '{w['name']}', found {len(matches)}")
    dest = matches[0]
    if not dest.get("verified"):
        abort(f"'{w['name']}' is not verified on Kraken yet")
    if _addr(dest) != w["address"]:
        abort(f"Kraken's saved '{w['name']}' does NOT match the built-in address.\n"
              f"  Kraken : {_addr(dest)}\n  Script : {w['address']}\nNothing sent.")
    fee = call("GET", f"/funding/v1/fees/{m['method_id']}",
               {"amount": amount_str, "fee_included": True})
    return w, asset, m, dest, fee


def _amt(x):
    a = (x or {}).get("asset_amount", x or {})
    return f"{a.get('amount', '?')} {((a.get('asset') or {}).get('name')) or ''}".strip()


def _show(coin, amount_str, w, m, dest, fee, mode):
    print("=" * 60)
    print(f"{mode} - Withdrawal")
    print("=" * 60)
    print(f"  Coin         : {coin}   (amount includes the fee)")
    print(f"  You debit    : {amount_str} {coin}")
    print(f"  Kraken fee   : {_amt(fee.get('fee'))}")
    print(f"  Arrives      : {_amt(fee.get('net_amount'))}")
    print(f"  Method       : {m.get('method_name')}")
    print(f"  To (name)    : {w['name']}")
    print(f"  To (address) : {_addr(dest)}")
    print("  Address check: matches the address in wallets.json")
    print()


def cmd_quote(coin, amount_str):
    w, asset, m, dest, fee = _resolve(coin, amount_str)
    _show(coin, amount_str, w, m, dest, fee, "DRY-RUN")
    print("Nothing was sent. To send for real, run:")
    print(f"  python my_withdraw.py send {coin} {amount_str}")


def cmd_send(coin, amount_str):
    w, asset, m, dest, fee = _resolve(coin, amount_str)
    _show(coin, amount_str, w, m, dest, fee, "LIVE")
    token = fee.get("withdrawal_fee_token")
    if not token:
        abort("Kraken did not return a fee token - nothing sent")
    tail = w["address"][-6:]
    print("!" * 60)
    print("  This sends REAL funds. Crypto withdrawals cannot be reversed.")
    print("!" * 60)
    if input("\nType the LAST 6 characters of the address to send: ").strip() != tail:
        print("Aborted. Nothing sent.")
        return
    _submit(coin, amount_str, w, asset, m, dest, fee)


def _submit(coin, amount_str, w, asset, m, dest, fee):
    """Send one already-resolved, already-confirmed withdrawal."""
    token = fee.get("withdrawal_fee_token")
    if not token:
        abort("Kraken did not return a fee token - nothing sent")
    body = {
        "scope": {"method_id": m["method_id"]},
        "address_id": dest["address_id"],
        "amount": {"asset_amount": {"asset": {"class": "currency", "name": asset},
                                    "amount": amount_str}},
        "fee": {"quoted_fee": {"token": token}, "fee_included": True},
        "expected_address": w["address"],
    }
    result = call("POST", "/funding/v1/withdrawals", body=body)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                            "coin": coin, "amount": amount_str, "to": w["name"],
                            "address": w["address"], "result": result}) + "\n")
    print(f"\nSubmitted. Withdrawal ID: {result.get('withdrawal_id')}")
    print(f"Arrives: {_amt(result.get('net_amount'))}  Fee: {_amt(result.get('fee'))}")


BALANCE_CODES = {"BTC": ["XXBT", "XBT"], "XRP": ["XXRP", "XRP"], "XLM": ["XXLM", "XLM"]}


def available_balances():
    """Spendable balance per built-in coin (balance minus amounts held by open orders)."""
    import krakenex
    cfg = dotenv_values(DIR / ".env")
    api = krakenex.API(key=cfg.get("KRAKEN_API_KEY"), secret=cfg.get("KRAKEN_API_SECRET"))
    resp = api.query_private("BalanceEx")
    if resp.get("error"):
        abort(f"Kraken BalanceEx: {resp['error']}")
    res = resp["result"]
    out = {}
    for coin, codes in BALANCE_CODES.items():
        b = next((res[c] for c in codes if c in res), None)
        out[coin] = (Decimal(b["balance"]) - Decimal(b.get("hold_trade", "0"))) if b else Decimal(0)
    return out


def cmd_menu():
    """Emergency menu: no commands to remember. You pick, then confirm each send."""
    print("\n  EMERGENCY WITHDRAW TO YOUR LEDGER")
    print("  =================================")
    bal = available_balances()
    coins = list(WALLETS)
    for i, c in enumerate(coins, 1):
        print(f"   {i}) {c:<4} available on Kraken: {bal[c]:f}")
    print(f"   {len(coins) + 1}) ALL of the above")
    print("   q) quit")
    pick = input("\n  Choose a number: ").strip().lower()
    if pick == "q" or not pick:
        print("  Quit. Nothing sent.")
        return
    if pick == str(len(coins) + 1):
        chosen = coins
    elif pick.isdigit() and 1 <= int(pick) <= len(coins):
        chosen = [coins[int(pick) - 1]]
    else:
        print("  Not a valid choice. Nothing sent.")
        return
    # 1) Check and quote every chosen coin first. Nothing is sent here.
    ready = []
    for c in chosen:
        amt = bal[c].quantize(Decimal("0.00000001"), rounding="ROUND_DOWN")
        if amt <= 0:
            print(f"\n  {c}: nothing on Kraken, skipping.")
            continue
        amt_s = format(amt.normalize(), "f")
        try:
            w, asset, m, dest, fee = _resolve(c, amt_s)
        except SystemExit:
            print(f"  {c} will NOT be sent (see message above).")
            continue
        _show(c, amt_s, w, m, dest, fee, "READY TO SEND")
        ready.append((c, amt_s, w, asset, m, dest, fee))
    if not ready:
        print("\n  Nothing to send. Done.")
        return
    # 2) One confirmation for everything listed above.
    print("!" * 60)
    print(f"  This sends REAL funds for: {', '.join(r[0] for r in ready)}")
    print("  Crypto withdrawals cannot be reversed.")
    print("!" * 60)
    if input("\n  Type CONFIRM to send everything listed above: ").strip().upper() != "CONFIRM":
        print("  Aborted. Nothing sent.")
        return
    # 3) Send each. If one fails, keep going with the rest.
    for c, amt_s, w, asset, m, dest, fee in ready:
        print(f"\n  ---- sending {c} ----")
        try:
            _submit(c, amt_s, w, asset, m, dest, fee)
        except SystemExit:
            print(f"  {c} was NOT sent (see message above). Continuing.")
    print("\n  Done. Check Ledger Wallet for arrivals (BTC can take 30-60 min).")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["menu"]:
        cmd_menu()
    elif a[:1] == ["probe"]:
        cmd_probe()
    elif len(a) == 3 and a[0] == "quote":
        cmd_quote(a[1].upper(), a[2])
    elif len(a) == 3 and a[0] == "send":
        cmd_send(a[1].upper(), a[2])
    else:
        print(__doc__)
