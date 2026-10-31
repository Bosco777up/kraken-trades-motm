"""my_reprice.py - change the price of an open limit order on your Kraken account.

  python my_reprice.py list    # read-only: numbered list of open orders
  python my_reprice.py menu    # REAL - pick order, type new price, type CONFIRM

How it works:
  1. Uses Kraken's AmendOrder: the same order just gets a new price (same order ID,
     coins never leave the order, nothing to re-place).
  2. If Kraken refuses the amend, it offers plan B: cancel the old order and place
     a new one at the new price (same amount, same settings) - asks CONFIRM again.
Only limit orders. Amount stays the same (what is left unfilled).
Claude never runs "menu"; it only runs "list".
"""
import json, sys, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import krakenex
from dotenv import dotenv_values

DIR = Path(__file__).resolve().parent
LOG = DIR / "logs" / "my_reprice_log.jsonl"


def abort(msg):
    print(f"\nABORTED: {msg}")
    sys.exit(1)


def api():
    cfg = dotenv_values(DIR / ".env")
    if not cfg.get("KRAKEN_API_KEY") or "paste_your" in cfg.get("KRAKEN_API_KEY", ""):
        abort("API key missing in .env")
    return krakenex.API(key=cfg["KRAKEN_API_KEY"], secret=cfg["KRAKEN_API_SECRET"])


def call(k, method, params=None, private=True):
    """Returns (result, errors). Never exits - caller decides."""
    try:
        r = (k.query_private if private else k.query_public)(method, params or {})
    except Exception as e:  # network etc.
        return None, [f"{type(e).__name__}: {e}"]
    return r.get("result"), r.get("error") or []


def q(k, method, params=None, private=True):
    res, err = call(k, method, params, private)
    if err:
        abort(f"Kraken {method}: {err}")
    return res


def log(entry):
    LOG.parent.mkdir(exist_ok=True)
    entry["time"] = datetime.now(timezone.utc).isoformat()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def open_orders(k):
    oo = q(k, "OpenOrders").get("open", {})
    rows = []
    for txid, o in sorted(oo.items(), key=lambda x: (x[1]["descr"]["pair"], x[1]["descr"]["type"], x[1]["descr"]["price"])):
        d = o["descr"]
        vol, done = Decimal(o["vol"]), Decimal(o["vol_exec"])
        rows.append({"txid": txid, "pair": d["pair"], "side": d["type"], "ordertype": d["ordertype"],
                     "price": d["price"], "left": vol - done, "vol": vol, "filled": done,
                     "oflags": o.get("oflags", "")})
    return rows


def show(rows):
    print(f"\n  {'#':<3}{'Pair':<10}{'Side':<6}{'Price':>16}{'Amount left':>22}   Order ID")
    for i, r in enumerate(rows, 1):
        print(f"  {i:<3}{r['pair']:<10}{r['side']:<6}{r['price']:>16}{format(r['left'], ',f').rstrip('0').rstrip('.'):>22}   {r['txid']}")
    print()


def pair_info(k, pair):
    info = next(iter(q(k, "AssetPairs", {"pair": pair}, private=False).values()))
    t = next(iter(q(k, "Ticker", {"pair": pair}, private=False).values()))
    return int(info["pair_decimals"]), Decimal(t["b"][0]), Decimal(t["a"][0]), Decimal(t["c"][0])


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        abort("cancelled - nothing changed")


def confirmed(prompt="  Type CONFIRM to go ahead (anything else cancels): "):
    return ask(prompt).upper() == "CONFIRM"


def place(k, o, price, volume):
    p = {"pair": o["pair"], "type": o["side"], "ordertype": "limit",
         "price": price, "volume": format(volume, "f")}
    if "post" in o["oflags"]:
        p["oflags"] = "post"
    return call(k, "AddOrder", p)


def plan_b(k, o, new_price):
    print("\n  PLAN B: cancel the old order, then place a new one at the new price.")
    print("  (For a second or two the coins are not in any order.)")
    if not confirmed():
        abort("cancelled - old order left as it was")
    _, err = call(k, "CancelOrder", {"txid": o["txid"]})
    if err:
        abort(f"cancel failed: {err} - old order left as it was")
    status, filled = None, o["filled"]
    for _ in range(10):
        time.sleep(1)
        res, err = call(k, "QueryOrders", {"txid": o["txid"]})
        if res and o["txid"] in res:
            status = res[o["txid"]]["status"]
            filled = Decimal(res[o["txid"]]["vol_exec"])
            if status in ("canceled", "closed"):
                break
    if status != "canceled":
        abort(f"old order status is '{status}', not cancelled - check 'orders' before doing anything else")
    left = o["vol"] - filled
    print(f"  Old order cancelled. Placing new order: {o['side']} {left} {o['pair']} @ {new_price}")
    res, err = place(k, o, new_price, left)
    log({"action": "plan_b", "old_txid": o["txid"], "pair": o["pair"], "side": o["side"],
         "old_price": o["price"], "new_price": new_price, "volume": str(left),
         "result": res, "error": err})
    if not err:
        print(f"\n  DONE. New order ID: {', '.join(res.get('txid', []))}")
        return
    print(f"\n  !!! NEW ORDER FAILED: {err}")
    print(f"  !!! The old order is CANCELLED. Your {o['pair']} is free, not in any order.")
    if confirmed(f"  Type CONFIRM to put the OLD order back ({o['side']} {left} @ {o['price']}): "):
        res, err = place(k, o, o["price"], left)
        log({"action": "restore_old", "pair": o["pair"], "price": o["price"], "result": res, "error": err})
        print(f"  {'Old order restored: ' + ', '.join(res.get('txid', [])) if not err else 'Restore FAILED: ' + str(err)}")
    else:
        print("  Nothing placed. Type 'orders' and 'balance' to check, or ask Claude.")


def menu(k):
    rows = open_orders(k)
    if not rows:
        abort("no open orders")
    print("\n  CHANGE THE PRICE OF AN OPEN ORDER")
    show(rows)
    pick = ask("  Order number (Enter to quit): ")
    if not pick.isdigit() or not 1 <= int(pick) <= len(rows):
        abort("no order picked - nothing changed")
    o = rows[int(pick) - 1]
    if o["ordertype"] != "limit":
        abort(f"only limit orders can be repriced here (this one is {o['ordertype']})")
    pdec, bid, ask_, last = pair_info(k, o["pair"])
    print(f"\n  {o['pair']} now: last {last}   bid {bid}   ask {ask_}")
    raw = ask(f"  New price (now {o['price']}): ").replace("$", "").replace(",", "")
    try:
        new = Decimal(raw)
    except InvalidOperation:
        abort(f"'{raw}' is not a price")
    if new <= 0:
        abort("price must be above 0")
    if -new.as_tuple().exponent > pdec:
        abort(f"{o['pair']} allows at most {pdec} decimal places")
    new_s = format(new.normalize(), "f")
    quote = o["pair"][-3:]
    print("\n  ------------------------------------------------------------")
    print(f"  Order    : {o['txid']}  ({o['side'].upper()} {o['pair']})")
    print(f"  Price    : {o['price']}  ->  {new_s}")
    print(f"  Amount   : {format(o['left'], ',f').rstrip('0').rstrip('.')} (unchanged)")
    print(f"  Worth    : about {o['left'] * new:,.2f} {quote} at the new price, before Kraken's fee")
    if last > 0:
        print(f"  vs market: {new / last:,.2f} x the last price")
    if o["side"] == "sell" and new <= bid:
        print(f"  WARNING  : at or below the bid ({bid}) - this would SELL RIGHT NOW at about market price.")
    if o["side"] == "buy" and new >= ask_:
        print(f"  WARNING  : at or above the ask ({ask_}) - this would BUY RIGHT NOW at about market price.")
    print("  ------------------------------------------------------------")
    if not confirmed():
        abort("cancelled - nothing changed")
    params = {"txid": o["txid"], "limit_price": new_s}
    if "post" in o["oflags"]:
        params["post_only"] = "true"
    res, err = call(k, "AmendOrder", params)
    log({"action": "amend", "txid": o["txid"], "pair": o["pair"], "side": o["side"],
         "old_price": o["price"], "new_price": new_s, "result": res, "error": err})
    if err:
        print(f"\n  Kraken refused the price change: {err}")
        return plan_b(k, o, new_s)
    time.sleep(2)
    now = q(k, "QueryOrders", {"txid": o["txid"]}).get(o["txid"], {})
    print(f"\n  DONE. {o['txid']} is now {now.get('descr', {}).get('order', '(check with: orders)')}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "menu"
    k = api()
    if cmd == "list":
        show(open_orders(k))
    elif cmd == "menu":
        menu(k)
    else:
        print(__doc__)
