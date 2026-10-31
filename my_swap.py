"""my_swap.py - swap one coin for another on your Kraken account.

Coins: BTC, XRP, XLM, LUNA, USD. Uses a direct Kraken market when one exists
(XRP/BTC, XLM/BTC, any coin/USD), otherwise goes through USD in two trades.

  python my_swap.py quote 50 XRP BTC        # test only - places nothing
  python my_swap.py swap  50 XRP BTC        # REAL - you run it, types CONFIRM
  python my_swap.py swap  50 XRP BTC 1.5    # optional max price move in % (default 1)

The amount is in the coin you are swapping FROM. Each trade is a limit order
at the current price plus/minus the max move, "immediate or cancel": it fills
right away up to that price or not at all, never left sitting on the book.
If the first trade of a two-step swap does not fill, it stops (no second trade).
Claude never runs "swap"; it only runs "quote".
"""
import json, sys, time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from pathlib import Path

import krakenex
from dotenv import dotenv_values

DIR = Path(__file__).resolve().parent
LOG = DIR / "logs" / "my_swap_log.jsonl"

COINS = {"BTC": "XBT", "XRP": "XRP", "XLM": "XLM", "LUNA": "LUNA", "USD": "USD"}
# Direct Kraken markets (altname -> base, quote), checked 2026-10-28.
PAIRS = {
    "XBTUSD": ("BTC", "USD"), "XRPUSD": ("XRP", "USD"), "XLMUSD": ("XLM", "USD"),
    "LUNAUSD": ("LUNA", "USD"), "XRPXBT": ("XRP", "BTC"), "XLMXBT": ("XLM", "BTC"),
}


def abort(msg):
    print(f"\nABORTED: {msg}")
    sys.exit(1)


def api():
    cfg = dotenv_values(DIR / ".env")
    if not cfg.get("KRAKEN_API_KEY") or "paste_your" in cfg.get("KRAKEN_API_KEY", ""):
        abort("API key missing in .env")
    return krakenex.API(key=cfg["KRAKEN_API_KEY"], secret=cfg["KRAKEN_API_SECRET"])


def q(k, method, params=None, private=True):
    r = (k.query_private if private else k.query_public)(method, params or {})
    if r.get("error"):
        abort(f"Kraken {method}: {r['error']}")
    return r["result"]


def route(frm, to):
    """List of legs: (pair_altname, side). side is 'sell' (give base) or 'buy' (get base)."""
    for alt, (base, quote) in PAIRS.items():
        if (base, quote) == (frm, to):
            return [(alt, "sell")]
        if (base, quote) == (to, frm):
            return [(alt, "buy")]
    legs = []
    if frm != "USD":
        alt = next((a for a, bq in PAIRS.items() if bq == (frm, "USD")), None)
        if not alt:
            abort(f"No {frm}/USD market")
        legs.append((alt, "sell"))
    if to != "USD":
        alt = next((a for a, bq in PAIRS.items() if bq == (to, "USD")), None)
        if not alt:
            abort(f"No {to}/USD market")
        legs.append((alt, "buy"))
    return legs


def market(k, alt):
    info = next(iter(q(k, "AssetPairs", {"pair": alt}, private=False).values()))
    t = next(iter(q(k, "Ticker", {"pair": alt}, private=False).values()))
    fee = q(k, "TradeVolume", {"pair": alt}).get("fees", {})
    fee_pct = Decimal(next(iter(fee.values()))["fee"]) if fee else Decimal("0.8")
    return {
        "alt": alt, "base": info["base"], "quote": info["quote"],
        "lot": int(info["lot_decimals"]), "pdec": int(info["pair_decimals"]),
        "ordermin": Decimal(str(info.get("ordermin", "0"))),
        "costmin": Decimal(str(info.get("costmin", "0"))),
        "bid": Decimal(t["b"][0]), "ask": Decimal(t["a"][0]),
        "fee": fee_pct / 100,
    }


def qd(x, places):
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


def plan_leg(m, side, give, slip):
    """Given an amount of the coin we GIVE on this leg, work out the order and the
    expected amount we GET (after Kraken's fee, which is charged in the quote coin)."""
    if side == "sell":                      # give base, get quote
        price = qd(m["bid"] * (1 - slip), m["pdec"])
        volume = qd(give, m["lot"])
        gross = volume * m["bid"]
        get = gross * (1 - m["fee"])
        cost = gross
    else:                                   # give quote, get base
        price = qd(m["ask"] * (1 + slip), m["pdec"])
        # spend <= give including fee, at the worst allowed price
        volume = qd(give / (price * (1 + m["fee"])), m["lot"])
        get = volume
        cost = volume * m["ask"]
    if volume < m["ordermin"]:
        abort(f"{m['alt']}: {volume} is below Kraken's minimum order of {m['ordermin']}")
    if cost < m["costmin"]:
        abort(f"{m['alt']}: order worth {cost:.6f} is below Kraken's minimum cost {m['costmin']}")
    return {"pair": m["alt"], "type": side, "ordertype": "limit",
            "price": format(price, "f"), "volume": format(volume, "f"),
            "timeinforce": "IOC"}, get


def available(k, code_hint):
    res = q(k, "BalanceEx")
    for code in (code_hint, "X" + code_hint, "Z" + code_hint):
        if code in res:
            b = res[code]
            return Decimal(b["balance"]) - Decimal(b.get("hold_trade", "0"))
    return Decimal(0)


def parse(argv):
    if len(argv) < 4:
        print(__doc__)
        sys.exit(0)
    try:
        amount = Decimal(argv[1])
    except InvalidOperation:
        abort(f"'{argv[1]}' is not a number")
    frm, to = argv[2].upper(), argv[3].upper()
    for c in (frm, to):
        if c not in COINS:
            abort(f"Unknown coin {c}. Use: {', '.join(COINS)}")
    if frm == to or amount <= 0:
        abort("Pick two different coins and a positive amount")
    slip = Decimal(argv[4]) / 100 if len(argv) > 4 else Decimal("0.01")
    if not (Decimal("0") < slip <= Decimal("0.05")):
        abort("Max price move must be between 0 and 5 percent")
    return amount, frm, to, slip


def plan(k, amount, frm, to, slip):
    legs = route(frm, to)
    have = available(k, COINS[frm])
    if amount > have:
        abort(f"You have {have:f} {frm} available (not counting coins held by open orders); "
              f"asked to swap {amount:f}")
    steps, give, coin = [], amount, frm
    for alt, side in legs:
        m = market(k, alt)
        order, get = plan_leg(m, side, give, slip)
        out = PAIRS[alt][1] if side == "sell" else PAIRS[alt][0]
        steps.append({"m": m, "order": order, "give": give, "give_coin": coin,
                      "get": get, "get_coin": out})
        give, coin = get, out
    return steps


def show(steps, frm, to, amount, slip, title):
    print("=" * 64)
    print(f"{title}: {amount:f} {frm} -> {to}   (max price move {slip * 100:f}%)")
    print("=" * 64)
    for i, s in enumerate(steps, 1):
        m, o = s["m"], s["order"]
        print(f"  Trade {i}: {o['type'].upper()} on {m['alt']}  "
              f"(bid {m['bid']}, ask {m['ask']}, fee {m['fee'] * 100:f}%)")
        print(f"     give  ~{qd(s['give'], 8):f} {s['give_coin']}")
        print(f"     limit {o['price']}  volume {o['volume']}  (immediate-or-cancel)")
        print(f"     get   ~{qd(s['get'], 8):f} {s['get_coin']}")
    print(f"\n  ESTIMATE you end with ~{qd(steps[-1]['get'], 8):f} {to}")
    print("  (Real result depends on the price at that moment, within the max move.)\n")


def validate(k, order):
    p = dict(order, validate="true")
    r = k.query_private("AddOrder", p)
    return r.get("error") or None, r.get("result", {}).get("descr", {}).get("order", "")


def log(entry):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(dict(entry, ts=datetime.now(timezone.utc).isoformat()),
                           default=str) + "\n")


def run_leg(k, order):
    """Place one IOC order and wait for Kraken to report it finished.
    Returns (vol_exec, cost, fee, txid) as Decimals."""
    res = q(k, "AddOrder", order)
    txid = res["txid"][0]
    for _ in range(20):
        time.sleep(1.5)
        o = q(k, "QueryOrders", {"txid": txid})[txid]
        if o.get("status") in ("closed", "canceled", "expired"):
            return (Decimal(o.get("vol_exec", "0")), Decimal(o.get("cost", "0")),
                    Decimal(o.get("fee", "0")), txid)
    abort(f"Order {txid} did not finish in time - check it with open-orders")


def cmd_quote(argv):
    amount, frm, to, slip = parse(argv)
    k = api()
    steps = plan(k, amount, frm, to, slip)
    show(steps, frm, to, amount, slip, "TEST SWAP")
    err, descr = validate(k, steps[0]["order"])
    print(f"  Kraken check of trade 1: {'FAILED ' + str(err) if err else 'OK - ' + descr}")
    if len(steps) > 1:
        print("  (Trade 2 is checked live, after trade 1 fills.)")
    print("\nNothing was placed. To swap for real, run:")
    print(f"  python my_swap.py swap {argv[1]} {frm} {to}" + (f" {argv[4]}" if len(argv) > 4 else ""))


def cmd_swap(argv):
    amount, frm, to, slip = parse(argv)
    k = api()
    steps = plan(k, amount, frm, to, slip)
    show(steps, frm, to, amount, slip, "LIVE SWAP")
    print("!" * 64)
    print(f"  This places {len(steps)} REAL trade(s) on Kraken.")
    print("!" * 64)
    if input("\n  Type CONFIRM to swap: ").strip().upper() != "CONFIRM":
        print("  Aborted. Nothing placed.")
        return
    give = amount
    for i, s in enumerate(steps, 1):
        order = s["order"]
        if i > 1:   # re-plan trade 2 with what trade 1 really produced + fresh prices
            m = market(k, order["pair"])
            order, _ = plan_leg(m, order["type"], give, slip)
            err, _ = validate(k, order)
            if err:
                abort(f"Trade 2 check failed: {err}. You now hold {give:f} {s['give_coin']}.")
        print(f"\n  Trade {i}: {order['type']} {order['volume']} on {order['pair']} "
              f"limit {order['price']} ...")
        vol, cost, fee, txid = run_leg(k, order)
        log({"swap": f"{amount} {frm}->{to}", "trade": i, "order": order,
             "txid": txid, "vol_exec": vol, "cost": cost, "fee": fee})
        if vol <= 0:
            abort(f"Trade {i} did not fill (price moved more than {slip * 100}%). "
                  f"Nothing else done. You hold {give:f} {s['give_coin']}.")
        got = (cost - fee) if order["type"] == "sell" else vol
        print(f"     filled {vol:f}  cost {cost:f}  fee {fee:f}  -> got {got:f} {s['get_coin']}  ({txid})")
        give = got
    print(f"\n  DONE. Swapped {amount:f} {frm} -> {give:f} {to}.")
    print("  To move it to your Ledger, use the EMERGENCY Withdraw icon (BTC/XRP/XLM).")


def cmd_menu():
    """Guided swap: no commands to remember."""
    k = api()
    print("\n  SWAP COINS ON KRAKEN")
    print("  ====================")
    coins = list(COINS)
    bal = {c: available(k, COINS[c]) for c in coins}
    for i, c in enumerate(coins, 1):
        print(f"   {i}) {c:<5} available: {bal[c]:f}")
    def pick(prompt):
        s = input(prompt).strip().upper()
        if s.isdigit() and 1 <= int(s) <= len(coins):
            return coins[int(s) - 1]
        return s if s in COINS else None
    frm = pick("\n  Swap FROM (number or name, Enter to quit): ")
    if not frm:
        print("  Quit. Nothing placed.")
        return
    to = pick("  Swap TO   (number or name): ")
    if not to or to == frm:
        print("  Not a valid choice. Nothing placed.")
        return
    amt = input(f"  How much {frm}? (number, or ALL for {bal[frm]:f}): ").strip().upper()
    if amt == "ALL":
        amt = format(bal[frm].normalize(), "f")
    argv = ["swap", amt, frm, to]
    print()
    cmd_swap(argv)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["menu"]:
        cmd_menu()
    elif a[:1] == ["quote"]:
        cmd_quote(a)
    elif a[:1] == ["swap"]:
        cmd_swap(a)
    else:
        print(__doc__)
