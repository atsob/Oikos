import logging
import math
import pandas as pd
from psycopg2.extras import execute_values
from database.connection import get_connection

log = logging.getLogger(__name__)

def update_accounts_balances(target_acc_id=None):
    """Update account balances based on transactions."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        if target_acc_id:
            # Investment-type accounts (Brokerage, Other Investment, Margin, Pension)
            # maintain their balance via update_investment_balances() /
            # update_pension_balances() which also reads the Investments table.
            # Overwriting with SUM(Transactions) alone would corrupt their balance.
            sql = """
                UPDATE Accounts a
                SET Accounts_Balance = COALESCE((
                    SELECT SUM(Total_Amount)
                    FROM Transactions t
                    WHERE t.Accounts_Id = a.Accounts_Id
                ), 0)
                WHERE a.Accounts_Id = %s
                  AND a.Accounts_Type NOT IN
                      ('Brokerage', 'Other Investment', 'Margin', 'Pension');
            """
            cur.execute(sql, (int(target_acc_id),))
        else:
            sql = """
                UPDATE Accounts a
                SET Accounts_Balance = COALESCE((
                    SELECT SUM(Total_Amount) 
                    FROM Transactions t 
                    WHERE t.Accounts_Id = a.Accounts_Id
                ), 0)
                WHERE a.Accounts_Type NOT IN ('Pension', 'Brokerage', 'Other Investment', 'Margin');
            """
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        log.exception("update_accounts_balances failed")
        raise
    finally:
        cur.close()
        conn.close()

def update_pension_balances():
    """Update pension account balances."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE Accounts a
            SET Accounts_Balance = COALESCE((
                SELECT
                    SUM(CASE WHEN Action IN ('CashIn', 'IntInc') THEN Total_Amount_AccCur
                             WHEN Action IN ('CashOut') THEN -Total_Amount_AccCur
                             ELSE 0 END)
                FROM Investments t
                WHERE t.Accounts_Id = a.Accounts_Id
            ), 0)
            WHERE a.Accounts_Type IN ('Pension');
        """)
        conn.commit()
    except Exception as e:
        log.exception("update_pension_balances failed")
        raise
    finally:
        cur.close()
        conn.close()

def update_investment_balances():
    """Update investment account balances."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE Accounts a
            SET Accounts_Balance = COALESCE((
                SELECT
                    SUM(CASE WHEN Action IN ('Dividend', 'CashIn', 'IntInc', 'MiscInc', 'Sell') THEN Total_Amount_AccCur
                             WHEN Action IN ('CashOut', 'MiscExp', 'Buy') THEN -Total_Amount_AccCur
                             ELSE 0 END)
                FROM Investments t
                WHERE t.Accounts_Id = a.Accounts_Id
				AND (t.Transactions_Id IS NULL OR t.Transactions_Id NOT IN (SELECT Transactions_Id FROM Transactions))                 
            ), 0) +  COALESCE((
                    SELECT SUM(Total_Amount) 
                    FROM Transactions t 
                    WHERE t.Accounts_Id = a.Accounts_Id
                ), 0)
            WHERE a.Accounts_Type IN ('Brokerage', 'Other Investment', 'Margin');
        """)
        conn.commit()
    except Exception as e:
        log.exception("update_investment_balances failed")
        raise
    finally:
        cur.close()
        conn.close()

_LINKED_TX_VIABLE_ACTIONS = frozenset({'Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp', 'MiscInc', 'CashOut', 'CashIn'})
_LINKED_TX_CASH_OUT      = frozenset({'Buy', 'MiscExp', 'CashOut'})


def _get_or_create_payee_in_cur(cur, name: str) -> "int | None":
    """Look up or insert a Payees row.  Commit is the caller's responsibility."""
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    cur.execute("SELECT Payees_Id FROM Payees WHERE Payees_Name = %s LIMIT 1", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO Payees (Payees_Name) VALUES (%s) RETURNING Payees_Id", (name,)
    )
    return cur.fetchone()[0]


def _resolve_saxo_charge_payee(cur) -> int:
    """Return the Payees_Id for account-level Saxo charges (VAT, CustodyFee, ...).

    Configurable via the ``saxo_charge_payee_id`` app setting (Importers →
    Saxo Bank → Account Charges); falls back to get-or-create a "Saxo Bank"
    payee if unset or the configured payee no longer exists.
    """
    from database.queries import get_app_setting
    configured = get_app_setting("saxo_charge_payee_id")
    if configured and configured.isdigit():
        cur.execute("SELECT 1 FROM Payees WHERE Payees_Id = %s", (int(configured),))
        if cur.fetchone():
            return int(configured)
    return _get_or_create_payee_in_cur(cur, "Saxo Bank")


def create_linked_cash_transactions_for_unlinked(
    acc_id: int,
    linked_acc_id: int,
) -> "tuple[int, list[str]]":
    """Create cash transactions on *linked_acc_id* for every unlinked investment
    entry in *acc_id* that has a viable action (Buy / Sell / Dividend / IntInc /
    RtrnCap / MiscExp) and then sets ``Investments.Transactions_Id`` to point to
    the newly created row.

    Designed to be called after an import commit so that investment accounts with
    a configured linked cash account automatically get their cash-side entries.

    Returns ``(created_count, error_list)``.  Callers should invoke
    ``update_investment_balances()`` and ``update_accounts_balances(linked_acc_id)``
    after this function returns.
    """
    conn = get_connection()
    cur  = conn.cursor()
    created = 0
    errors: list[str] = []

    try:
        cur.execute(
            """
            SELECT i.investments_id,
                   i.date,
                   i.action,
                   i.total_amount_acccur,
                   i.description,
                   s.securities_name
            FROM   Investments i
            LEFT   JOIN Securities s ON s.securities_id = i.securities_id
            WHERE  i.accounts_id     = %s
              AND  i.transactions_id IS NULL
              AND  i.action IN ('Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp', 'MiscInc', 'CashOut', 'CashIn')
            ORDER  BY i.date, i.investments_id
            """,
            (acc_id,),
        )
        rows = cur.fetchall()

        for inv_id, inv_date, action, total_acc, description, sec_name in rows:
            cur.execute("SAVEPOINT cltx_sp")
            try:
                # Account-level charges (VAT, CustodyFee, ...) have no security and
                # their Description is an internal dedup key (e.g.
                # "SAXO|CHARGE|VAT||2026-06-01|0_0200"), not a payee name — use a
                # broker-level payee and a readable charge-type label instead of
                # surfacing the raw key.
                if description and description.startswith("SAXO|CHARGE|"):
                    payee_id    = _resolve_saxo_charge_payee(cur)
                    charge_type = description.split("|")[2] if description.count("|") >= 2 else ""
                    desc_label  = f"Saxo {charge_type}".strip() if charge_type else "Saxo Bank"
                else:
                    payee_label = sec_name or description or action or ""
                    desc_label  = payee_label
                    payee_id    = _get_or_create_payee_in_cur(cur, payee_label)
                total_f   = float(total_acc or 0)
                cash_sign = -abs(total_f) if action in _LINKED_TX_CASH_OUT else abs(total_f)

                cur.execute(
                    """
                    INSERT INTO Transactions
                        (Accounts_Id, Date, Payees_Id, Description,
                         Total_Amount, Cleared,
                         Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                    VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, NULL)
                    RETURNING Transactions_Id
                    """,
                    (linked_acc_id, inv_date, payee_id, desc_label or action,
                     cash_sign, acc_id, abs(total_f)),
                )
                tx_id = cur.fetchone()[0]

                cur.execute(
                    "UPDATE Investments SET Transactions_Id = %s WHERE Investments_Id = %s",
                    (tx_id, inv_id),
                )
                cur.execute("RELEASE SAVEPOINT cltx_sp")
                created += 1
            except Exception as row_err:
                cur.execute("ROLLBACK TO SAVEPOINT cltx_sp")
                errors.append(f"Inv #{inv_id}: {row_err}")

        conn.commit()

        # Recalculate balances for both sides after all cash transactions are created.
        # update_accounts_balances recalculates the linked cash account from Transactions.
        # update_investment_balances recalculates the investment account from Investments.
        update_accounts_balances(linked_acc_id)
        update_investment_balances()

    except Exception as outer_err:
        conn.rollback()
        errors.append(f"Outer error: {outer_err}")
    finally:
        cur.close()
        conn.close()

    return created, errors


def get_linked_account_id(acc_id: int) -> "int | None":
    """Return ``Accounts_Id_Linked`` for *acc_id*, or ``None`` if not set."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT Accounts_Id_Linked FROM Accounts WHERE Accounts_Id = %s",
            (acc_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        cur.close()
        conn.close()


def update_holdings():
    """Update holdings based on investment transactions.

    Runs a signed-lot FIFO simulation per (account, security) to find the
    currently open position and its cost basis. The previous implementation
    guessed "long mode" vs "short mode" once from the aggregate
    total-buys-vs-total-sells sign for the account's entire history, which
    silently broke in two ways: (1) any account that went both long and
    short over its lifetime (round-trip through a short position) could be
    mis-costed for the currently-open lots, and (2) negative-quantity
    Reinvest rows (e.g. daily interest-accrual reversals on cash-fund
    holdings) skewed the aggregate sign check enough to misclassify the
    whole position, in one observed case making a real ~2.8-unit holding
    disappear entirely (Quantity computed as 0). Simulating the lot queue
    transaction-by-transaction (as already done for realized P&L in
    database.queries and api.routers.reports) sidesteps both problems.
    """
    from collections import deque
    BUY_ACTIONS  = {'Buy', 'Reinvest', 'ShrIn'}
    SELL_ACTIONS = {'Sell', 'ShrOut'}
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            DELETE FROM Holdings
            WHERE NOT EXISTS (
                SELECT 1
                FROM Investments i
                WHERE i.Accounts_Id  = Holdings.Accounts_Id
                  AND i.Securities_Id = Holdings.Securities_Id
                  AND i.Securities_Id IS NOT NULL
            )
        """)

        df = pd.read_sql("""
            SELECT i.Accounts_Id AS accounts_id, i.Securities_Id AS securities_id,
                   i.Date AS date, i.Investments_Id AS investments_id,
                   i.Action AS action, i.Quantity AS quantity,
                   i.Price_Per_Share AS price_per_share,
                   i.Total_Amount_AccCur AS total_amount_acccur,
                   i.FX_Rate AS fx_rate
            FROM Investments i
            WHERE i.Securities_Id IS NOT NULL
              AND i.Action IN ('Buy', 'Reinvest', 'ShrIn', 'Sell', 'ShrOut')
            ORDER BY i.Accounts_Id, i.Securities_Id, i.Date, i.Investments_Id
        """, conn)

        rows_to_upsert = []
        for (acc_id, sec_id), grp in df.groupby(['accounts_id', 'securities_id'], sort=False):
            long_lots: deque = deque()
            short_lots: deque = deque()
            # Simple Avg = a running weighted-average cost, blended on every buy and left
            # unchanged (only qty shrinks) on every sell — RESETTING to zero whenever the
            # position closes out exactly, so a fully-sold-and-later-rebought position
            # starts its average fresh rather than dragging in cost basis from units that
            # aren't held anymore. This mirrors _compute_wac_gains in api/routers/reports.py.
            # Two earlier versions were both wrong: an unweighted mean of Price_Per_Share
            # across buy *rows* (one 0.001-unit buy counted as much as a 100-unit buy), and
            # then a quantity-weighted mean across *every* buy ever with no reset (still
            # blending in cost basis from units sold off long ago). Observed on a Bitcoin
            # holding bought in 2016-2017 at €374-846, fully sold twice (2017, 2020), then
            # rebought entirely from 2025 onward at ~€70k-103k: both prior versions
            # produced numbers nowhere near the true current cost basis of ~€86,470/unit.
            wac_long_qty, wac_long_avg = 0.0, 0.0
            wac_short_qty, wac_short_avg = 0.0, 0.0

            for row in grp.itertuples():
                qty = float(row.quantity) if pd.notna(row.quantity) else 0.0
                if abs(qty) <= 1e-12:
                    continue

                total_acccur = float(row.total_amount_acccur) if pd.notna(row.total_amount_acccur) else 0.0
                fx    = float(row.fx_rate) if pd.notna(row.fx_rate) else 1.0
                price = float(row.price_per_share) if pd.notna(row.price_per_share) else 0.0
                if total_acccur != 0:
                    price_sec = total_acccur / (fx * qty) if fx else price
                    price_eur = total_acccur / qty
                else:
                    price_sec = price
                    price_eur = price * fx

                # A "buy-type" action can still carry a negative quantity (interest-accrual
                # reversal rows), which must behave like a small sell — so classify by the
                # signed delta, not by the action label alone.
                delta = qty if row.action in BUY_ACTIONS else -qty

                if delta > 1e-12:
                    remaining = delta
                    while remaining > 1e-12 and short_lots:
                        lot = short_lots[0]
                        consumed = min(lot['qty'], remaining)
                        lot['qty'] -= consumed
                        remaining -= consumed
                        if lot['qty'] < 1e-12:
                            short_lots.popleft()
                    if remaining > 1e-12:
                        long_lots.append({'qty': remaining, 'price_sec': price_sec, 'price_eur': price_eur})

                    wac_remaining = delta
                    if wac_short_qty > 1e-9:
                        cover = min(wac_short_qty, wac_remaining)
                        wac_short_qty -= cover
                        wac_remaining -= cover
                        if wac_short_qty < 1e-9:
                            wac_short_qty, wac_short_avg = 0.0, 0.0
                    if wac_remaining > 1e-9:
                        new_qty = wac_long_qty + wac_remaining
                        wac_long_avg = (wac_long_qty * wac_long_avg + wac_remaining * price_sec) / new_qty
                        wac_long_qty = new_qty
                elif delta < -1e-12:
                    remaining = -delta
                    while remaining > 1e-12 and long_lots:
                        lot = long_lots[0]
                        consumed = min(lot['qty'], remaining)
                        lot['qty'] -= consumed
                        remaining -= consumed
                        if lot['qty'] < 1e-12:
                            long_lots.popleft()
                    if remaining > 1e-12:
                        short_lots.append({'qty': remaining, 'price_sec': price_sec, 'price_eur': price_eur})

                    wac_remaining = -delta
                    if wac_long_qty > 1e-9:
                        close = min(wac_long_qty, wac_remaining)
                        wac_long_qty -= close
                        wac_remaining -= close
                        if wac_long_qty < 1e-9:
                            wac_long_qty, wac_long_avg = 0.0, 0.0
                    if wac_remaining > 1e-9:
                        new_qty = wac_short_qty + wac_remaining
                        wac_short_avg = (wac_short_qty * wac_short_avg + wac_remaining * price_sec) / new_qty
                        wac_short_qty = new_qty

            long_qty  = sum(l['qty'] for l in long_lots)
            short_qty = sum(l['qty'] for l in short_lots)
            if long_qty > 1e-9:
                net_qty       = long_qty
                fifo_price_sec = sum(l['qty'] * l['price_sec'] for l in long_lots) / long_qty
                fifo_price_eur = sum(l['qty'] * l['price_eur'] for l in long_lots) / long_qty
            elif short_qty > 1e-9:
                net_qty       = -short_qty
                fifo_price_sec = sum(l['qty'] * l['price_sec'] for l in short_lots) / short_qty
                fifo_price_eur = sum(l['qty'] * l['price_eur'] for l in short_lots) / short_qty
            else:
                net_qty, fifo_price_sec, fifo_price_eur = 0.0, 0.0, 0.0

            if wac_long_qty > 1e-9:
                simple_avg = wac_long_avg
            elif wac_short_qty > 1e-9:
                simple_avg = wac_short_avg
            else:
                simple_avg = 0.0
            rows_to_upsert.append((int(acc_id), int(sec_id), net_qty, simple_avg, fifo_price_sec, fifo_price_eur))

        if rows_to_upsert:
            execute_values(cur, """
                INSERT INTO Holdings (Accounts_Id, Securities_Id, Quantity, Simple_Avg_Price, Fifo_Avg_Price, Fifo_Avg_Cost_EUR)
                VALUES %s
                ON CONFLICT (Accounts_Id, Securities_Id) DO UPDATE SET
                    Quantity          = EXCLUDED.Quantity,
                    Simple_Avg_Price  = EXCLUDED.Simple_Avg_Price,
                    Fifo_Avg_Price    = EXCLUDED.Fifo_Avg_Price,
                    Fifo_Avg_Cost_EUR = EXCLUDED.Fifo_Avg_Cost_EUR,
                    Last_Update       = CURRENT_TIMESTAMP
            """, rows_to_upsert)

        # Remove zero-quantity Holdings rows that have no remaining investments
        # (e.g. after all transactions for a security have been moved or deleted).
        # Holdings with Quantity=0 that still have Investments rows are kept
        # intentionally for closed-position P&L history.
        cur.execute("""
            DELETE FROM Holdings
            WHERE ABS(Quantity) = 0
              AND NOT EXISTS (
                SELECT 1
                FROM Investments i
                WHERE i.Accounts_Id   = Holdings.Accounts_Id
                  AND i.Securities_Id  = Holdings.Securities_Id
                  AND i.Securities_Id IS NOT NULL
              )
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("update_holdings failed")
        raise
    finally:
        cur.close()
        conn.close()


def _lookup_hist_price(cur, securities_id, date):
    """Return the most recent closing price for a security on or before date."""
    cur.execute("""
        SELECT Close FROM Historical_Prices
        WHERE Securities_Id = %s AND Date <= %s
        ORDER BY Date DESC LIMIT 1
    """, (securities_id, date))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def _lookup_hist_fx(cur, accounts_id, date):
    """Return the most recent account-currency → EUR FX rate on or before date."""
    cur.execute("""
        SELECT hfx.FX_Rate
        FROM Historical_FX hfx
        JOIN Accounts a ON a.Currencies_Id = hfx.Currencies_Id_1
        WHERE a.Accounts_Id = %s AND hfx.Date <= %s
        ORDER BY hfx.Date DESC LIMIT 1
    """, (accounts_id, date))
    row = cur.fetchone()
    return float(row[0]) if row else 1.0


def insert_staking_reinvest(entries):
    """Insert Reinvest investment entries for staking rewards.

    entries: list of dicts with keys:
        accounts_id, securities_id, quantity, price_per_share, date

    When price_per_share is 0 or missing, the most recent closing price from
    Historical_Prices is used automatically so that P&L calculations capture
    the fair-market value of the staking income at the time of receipt.
    """
    if not entries:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        for e in entries:
            qty   = float(e['quantity'])
            price = float(e.get('price_per_share') or 0)

            # Auto-populate price from Historical_Prices when not provided
            if price == 0 and qty > 0:
                price = _lookup_hist_price(cur, e['securities_id'], e['date'])

            fx_rate   = _lookup_hist_fx(cur, e['accounts_id'], e['date'])
            total_sec = qty * price                # amount in security currency
            total_acc = total_sec * fx_rate        # amount in account currency

            cur.execute("""
                INSERT INTO Investments
                    (Accounts_Id, Securities_Id, Date, Action, Quantity,
                     Price_Per_Share, Commission,
                     Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate, Description)
                VALUES (%s, %s, %s, 'Reinvest', %s, %s, 0, %s, %s, %s, 'Staking reward')
            """, (e['accounts_id'], e['securities_id'], e['date'],
                  qty, price, total_acc, total_sec, fx_rate))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        cur.close()
        conn.close()
    update_holdings()


def update_db_stats():
    """Update database statistics."""
    conn = get_connection()
    try:
        # Χρήση επιπέδου απομόνωσης που επιτρέπει το ANALYZE αν χρειαστεί
        old_isolation_level = conn.isolation_level
        conn.set_isolation_level(0) # autocommit mode
        
        with conn.cursor() as cursor:
            cursor.execute("ANALYZE;")
        
        conn.set_isolation_level(old_isolation_level)
        print("Database statistics updated successfully.")
    except Exception as e:
        print(f"Error updating stats: {e}")

# Καλέστε το στο τέλος του import process:
# update_db_stats()


def delete_historical_prices(rows: list):
    """Delete specific Historical_Prices rows by (securities_id, date) pairs."""
    if not rows:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            "DELETE FROM Historical_Prices WHERE Securities_Id = %s AND Date = %s",
            [(int(r['securities_id']), r['date']) for r in rows],
        )
        deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        cur.close()
        conn.close()


def insert_prices_from_transactions(rows: list) -> int:
    """Insert Historical_Prices rows derived from investment transaction prices.

    Each element of *rows* must have 'securities_id', 'date', 'price'.
    Existing rows are left untouched (ON CONFLICT DO NOTHING).
    Returns the number of rows actually inserted.
    """
    if not rows:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            """
            INSERT INTO Historical_Prices (Securities_Id, Date, Close, Source, Downloaded_At)
            VALUES (%s, %s, %s, 'Transactions', NOW())
            ON CONFLICT (Securities_Id, Date) DO NOTHING
            """,
            [(int(r['securities_id']), r['date'], float(r['price'])) for r in rows],
        )
        inserted = cur.rowcount
        conn.commit()
        return inserted
    finally:
        cur.close()
        conn.close()


def resolve_investment_fx(cur, total_acc_cur, acc_currencies_id, sec_currencies_id,
                          trade_date, explicit_fx_rate=None):
    """Return ``(total_sec_cur, fx_rate)`` for an Investments row.

    FX_Rate convention: account-currency units per 1 security-currency unit.
    Example: for a USD security in a EUR account, FX_Rate ≈ 0.92 (EUR per USD).
    total_sec_cur = total_acc_cur / fx_rate.

    Parameters
    ----------
    cur               : open DB cursor
    total_acc_cur     : total amount already expressed in account currency
    acc_currencies_id : Currencies_Id of the investment account
    sec_currencies_id : Currencies_Id of the security (may be None)
    trade_date        : date of the trade (datetime.date or ISO string)
    explicit_fx_rate  : caller-supplied rate (e.g. from a broker CSV); when
                        provided and not trivially 1.0, it is used directly.
    """
    # Trivial case — same currency or unknown security currency
    if sec_currencies_id is None or sec_currencies_id == acc_currencies_id:
        return total_acc_cur, 1.0

    # Caller already knows the rate
    if explicit_fx_rate and float(explicit_fx_rate) not in (0.0, 1.0):
        fx = float(explicit_fx_rate)
        total_sec = round(total_acc_cur / fx, 18) if fx else total_acc_cur
        return total_sec, fx

    # Look up Historical_FX: sec → acc direction first
    cur.execute(
        """SELECT fx_rate FROM Historical_FX
           WHERE currencies_id_1 = %s AND currencies_id_2 = %s
             AND date <= COALESCE(%s::date, CURRENT_DATE)
           ORDER BY date DESC LIMIT 1""",
        (sec_currencies_id, acc_currencies_id, trade_date),
    )
    row = cur.fetchone()
    if row:
        fx = float(row[0])
        return round(total_acc_cur / fx, 18) if fx else total_acc_cur, fx

    # Try the inverse direction
    cur.execute(
        """SELECT fx_rate FROM Historical_FX
           WHERE currencies_id_1 = %s AND currencies_id_2 = %s
             AND date <= COALESCE(%s::date, CURRENT_DATE)
           ORDER BY date DESC LIMIT 1""",
        (acc_currencies_id, sec_currencies_id, trade_date),
    )
    row = cur.fetchone()
    if row and float(row[0]):
        fx = 1.0 / float(row[0])
        return round(total_acc_cur / fx, 18), fx

    # No FX data available — treat as 1:1
    return total_acc_cur, 1.0


def normalize_investment_prices(investments_ids: list) -> int:
    """Normalize Quantity and Price_Per_Share for investment transactions using Historical_Prices.

    Two-phase update so positions close correctly:

    Phase 1 — Buy / Reinvest / ShrIn:
        Price_Per_Share = Historical_Prices.Close on that date
        Quantity        = Total_Amount_SecCur / Close  (falls back to Total_Amount_AccCur when NULL)

    Phase 2 — Sell / ShrOut:
        Quantity is distributed proportionally from the total normalised buy quantity
        for the same (account, security), so sum(sell_qty) = sum(buy_qty) and the
        position closes.  Price_Per_Share is back-computed as Total_Amount_SecCur / Quantity
        (effective realised price, which may differ from the hist close).

    Total_Amount_AccCur is never modified, so account balances are preserved.
    Returns the total number of rows updated (buys + sells).
    """
    if not investments_ids:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    try:
        # ── Phase 1: normalise buy-side rows ─────────────────────────────────
        cur.execute(
            """
            UPDATE Investments i
               SET Price_Per_Share = hp.Close,
                   Quantity        = ROUND(
                       (COALESCE(i.Total_Amount_SecCur, i.Total_Amount_AccCur)
                        / NULLIF(hp.Close, 0))::numeric, 6)
              FROM Historical_Prices hp
             WHERE hp.Securities_Id = i.Securities_Id
               AND hp.Date          = i.Date
               AND i.Action IN ('Buy', 'Reinvest', 'ShrIn')
               AND i.Investments_Id = ANY(%s)
            """,
            (investments_ids,),
        )
        buy_updated = cur.rowcount

        # ── Phase 2: normalise sell-side rows ─────────────────────────────────
        # Sell qty is distributed proportionally from the total normalised buy qty
        # for the same (account, security), so sum(sell_qty) = sum(buy_qty) and the
        # position closes correctly.
        #
        # IMPORTANT: use ABS(Total_Amount) throughout so that sells whose
        # Total_Amount is negative (e.g. a losing CFD trade) still produce a
        # positive quantity.  Price is back-computed as ABS(Total_Amount) / Quantity
        # so that Quantity × Price_Per_Share = ABS(Total_Amount).
        cur.execute(
            """
            WITH buy_totals AS (
                -- Sum of already-normalised buy quantities for each (account, security)
                -- that has at least one sell being normalised now.
                SELECT i.Accounts_Id, i.Securities_Id,
                       SUM(i.Quantity) AS total_buy_qty
                FROM Investments i
                WHERE i.Action IN ('Buy', 'Reinvest', 'ShrIn')
                  AND EXISTS (
                      SELECT 1 FROM Investments s2
                      WHERE s2.Investments_Id = ANY(%s)
                        AND s2.Action IN ('Sell', 'ShrOut')
                        AND s2.Accounts_Id   = i.Accounts_Id
                        AND s2.Securities_Id = i.Securities_Id
                  )
                GROUP BY i.Accounts_Id, i.Securities_Id
            ),
            sell_totals AS (
                -- Use ABS so mixed-sign amounts (losing trades) don't cancel
                -- each other out or invert the proportional weight.
                -- Prefer Total_Amount_SecCur (security-native) for accuracy; fall back
                -- to Total_Amount_AccCur when not yet populated.
                SELECT Accounts_Id, Securities_Id,
                       SUM(ABS(COALESCE(Total_Amount_SecCur, Total_Amount_AccCur))) AS total_sell_amt_abs
                FROM Investments
                WHERE Action IN ('Sell', 'ShrOut')
                  AND Investments_Id = ANY(%s)
                GROUP BY Accounts_Id, Securities_Id
            )
            UPDATE Investments i
               SET Quantity        = ROUND(
                       (bt.total_buy_qty
                        * (ABS(COALESCE(i.Total_Amount_SecCur, i.Total_Amount_AccCur))
                           / NULLIF(st.total_sell_amt_abs, 0)))::numeric,
                       6),
                   Price_Per_Share = ROUND(
                       (ABS(COALESCE(i.Total_Amount_SecCur, i.Total_Amount_AccCur))
                        / NULLIF(bt.total_buy_qty
                                 * (ABS(COALESCE(i.Total_Amount_SecCur, i.Total_Amount_AccCur))
                                    / NULLIF(st.total_sell_amt_abs, 0)),
                                 0))::numeric,
                       4)
              FROM buy_totals bt
              JOIN sell_totals st
                   ON  st.Accounts_Id   = bt.Accounts_Id
                   AND st.Securities_Id = bt.Securities_Id
             WHERE i.Accounts_Id   = bt.Accounts_Id
               AND i.Securities_Id = bt.Securities_Id
               AND i.Action IN ('Sell', 'ShrOut')
               AND i.Investments_Id = ANY(%s)
            """,
            (investments_ids, investments_ids, investments_ids),
        )
        sell_updated = cur.rowcount

        conn.commit()

        # Refresh Holdings so the portfolio view is immediately consistent.
        update_holdings()

        return buy_updated + sell_updated
    finally:
        cur.close()
        conn.close()


# =============================================================================
# STOCK SPLIT / REVERSE SPLIT


def apply_stock_split(
    securities_id: int,
    split_date,
    new_shares: float,
    old_shares: float,
    holdings_by_account: list,
    description: str = "",
) -> int:
    """
    Record a stock split (or reverse split) by inserting a ShrIn / ShrOut row
    for the *delta* shares on the split date, one row per account.
    Historical broker records are NOT modified.

    holdings_by_account: list of dicts/rows with keys accounts_id, current_qty.

    Forward split (new > old): delta = current_qty * (ratio - 1)  -> ShrIn
    Reverse split (new < old): delta = current_qty * (1 - ratio)  -> ShrOut

    Returns the number of rows inserted.
    """
    ratio = new_shares / old_shares
    is_forward = new_shares >= old_shares
    action = "ShrIn" if is_forward else "ShrOut"
    label = description or (
        f"{int(new_shares)}:{int(old_shares)} "
        + ("Stock Split" if is_forward else "Reverse Split")
    )

    action_type = "Split" if is_forward else "Reverse Split"

    conn = get_connection()
    cur = conn.cursor()
    try:
        # ── 1. Insert Corporate_Actions event record ───────────────────────────
        cur.execute("""
            INSERT INTO Corporate_Actions
                (Securities_Id, Action_Type, Effective_Date,
                 Ratio_New, Ratio_Old, Description)
            VALUES (%s, %s::corporate_action_type, %s, %s, %s, %s)
        """, (securities_id, action_type, split_date, new_shares, old_shares, label))

        # ── 2. Insert ShrIn / ShrOut per account ──────────────────────────────
        inserted = 0
        for row in holdings_by_account:
            acct_id = int(row["accounts_id"])
            current_qty = float(row["current_qty"])
            if current_qty <= 0:
                continue

            if is_forward:
                delta = round(current_qty * (ratio - 1), 10)
            else:
                delta = round(current_qty * (1 - ratio), 10)

            if delta <= 0:
                continue

            cur.execute("""
                INSERT INTO Investments
                    (Accounts_Id, Securities_Id, Date, Action,
                     Quantity, Price_Per_Share, Commission,
                     Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                     Description)
                VALUES (%s, %s, %s, %s, %s, 0, 0, 0, 0, 1, %s)
            """, (acct_id, securities_id, split_date, action, delta, label))
            inserted += 1

        conn.commit()
        update_holdings()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# WRITE-OFF  (Default / Delisting)


def apply_writeoff(
    securities_id: int,
    event_date,
    action_type: str,          # 'Default' | 'Delisting'
    holdings_by_account: list,
    description: str = "",
) -> int:
    """
    Write off all remaining shares of a security to zero by inserting a ShrOut
    for the full current quantity per account, and recording a Corporate_Actions
    event of type 'Default' or 'Delisting'.

    holdings_by_account: list of dicts with keys accounts_id, current_qty.

    Returns the number of ShrOut rows inserted.
    """
    label = description or f"{action_type} — shares written off to zero"

    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Insert Corporate_Actions event record
        cur.execute("""
            INSERT INTO Corporate_Actions
                (Securities_Id, Action_Type, Effective_Date,
                 Ratio_New, Ratio_Old, Description)
            VALUES (%s, %s::corporate_action_type, %s, NULL, NULL, %s)
        """, (securities_id, action_type, event_date, label))

        # 2. Insert a ShrOut for the full position per account
        inserted = 0
        for row in holdings_by_account:
            acct_id = int(row["accounts_id"])
            current_qty = float(row["current_qty"])
            if current_qty <= 0:
                continue

            cur.execute("""
                INSERT INTO Investments
                    (Accounts_Id, Securities_Id, Date, Action,
                     Quantity, Price_Per_Share, Commission,
                     Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                     Description)
                VALUES (%s, %s, %s, 'ShrOut', %s, 0, 0, 0, 0, 1, %s)
            """, (acct_id, securities_id, event_date, current_qty, label))
            inserted += 1

        conn.commit()
        update_holdings()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()



# =============================================================================
# DIVIDEND
# =============================================================================


def apply_dividend(
    securities_id: int,
    event_date,
    gross_per_share: float,
    tax_rate_pct: float,        # e.g. 15.0 for 15 %
    holdings_by_account: list,  # dicts with accounts_id, current_qty
    description: str = "",
) -> int:
    """
    Record a dividend payment by inserting a Dividend investment row per account.

    Price_Per_Share     = net_per_share  (gross × (1 - tax_rate/100))
    Total_Amount_SecCur = net total    (qty × net_per_share)
    Total_Amount_AccCur = net total    (same — keeps all three columns consistent at FX 1.0)

    Also logs a Corporate_Actions record of type 'Dividend'.

    Returns the number of Dividend rows inserted.
    """
    net_factor = 1.0 - tax_rate_pct / 100.0
    label = description or f"Dividend — {gross_per_share} gross per share"

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO Corporate_Actions
                (Securities_Id, Action_Type, Effective_Date, Description)
            VALUES (%s, 'Dividend'::corporate_action_type, %s, %s)
        """, (securities_id, event_date, label))

        inserted = 0
        for row in holdings_by_account:
            acct_id = int(row["accounts_id"])
            qty = float(row["current_qty"])
            if qty <= 0:
                continue

            net_per_share = gross_per_share * net_factor
            net_total     = qty * net_per_share

            cur.execute("""
                INSERT INTO Investments
                    (Accounts_Id, Securities_Id, Date, Action,
                     Quantity, Price_Per_Share, Commission,
                     Total_Amount_SecCur, Total_Amount_AccCur, FX_Rate,
                     Description)
                VALUES (%s, %s, %s, 'Dividend', %s, %s, 0, %s, %s, 1, %s)
            """, (acct_id, securities_id, event_date,
                  qty, net_per_share,
                  net_total, net_total, label))
            inserted += 1

        conn.commit()
        update_holdings()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# =============================================================================
# RECURRING TEMPLATES
# =============================================================================
# Column naming follows the schema convention: plural-prefix for all PKs/FKs.
#   Recurring_Templates PK  → Templates_Id
#   Recurring_Template_Splits FK → Templates_Id
#   Transactions FK         → Templates_Id

def get_recurring_templates() -> pd.DataFrame:
    """Return all templates (header-level) ordered by active then next due date."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t.Templates_Id,
                    t.Name,
                    t.Accounts_Id,
                    a.Accounts_Name,
                    t.Payees_Id,
                    p.Payees_Name,
                    t.Description,
                    t.Total_Amount,
                    t.Periodicity,
                    t.Next_Due_Date,
                    t.End_Date,
                    t.Auto_Confirm,
                    t.Active,
                    t.Accounts_Id_Target,
                    t.Created_At
                FROM Recurring_Templates t
                JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                ORDER BY t.Active DESC, t.Next_Due_Date ASC
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(
            columns=['templates_id','name','accounts_id','accounts_name','payees_id','payees_name',
                     'description','total_amount','periodicity','next_due_date','end_date',
                     'auto_confirm','active','accounts_id_target','created_at']
        )
    finally:
        conn.close()


def get_template_splits(templates_id: int) -> pd.DataFrame:
    """Return splits for one template with full recursive category path."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH RECURSIVE ch AS (
                    SELECT Categories_Id, Categories_Name::TEXT AS full_path
                    FROM   Categories
                    WHERE  Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                    FROM   Categories c
                    JOIN   ch ON c.Categories_Id_Parent = ch.Categories_Id
                )
                SELECT s.Splits_Id, s.Templates_Id, s.Categories_Id,
                       ch.full_path AS categories_name, s.Amount, s.Memo
                FROM Recurring_Template_Splits s
                LEFT JOIN ch ON ch.Categories_Id = s.Categories_Id
                WHERE s.Templates_Id = %s
                ORDER BY s.Splits_Id
            """, (templates_id,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(
            columns=['splits_id','templates_id','categories_id','categories_name','amount','memo']
        )
    finally:
        conn.close()


def save_recurring_template(template: dict, splits: list) -> int:
    """Upsert a template and replace its splits atomically. Returns templates_id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        tid = template.get('templates_id')
        if tid:
            cur.execute("""
                UPDATE Recurring_Templates SET
                    Name = %s, Accounts_Id = %s, Payees_Id = %s, Description = %s,
                    Total_Amount = %s, Periodicity = %s, Next_Due_Date = %s,
                    End_Date = %s, Auto_Confirm = %s, Active = %s, Accounts_Id_Target = %s
                WHERE Templates_Id = %s
            """, (
                template['name'], template['accounts_id'], template.get('payees_id'),
                template.get('description'), template.get('total_amount'),
                template['periodicity'], template['next_due_date'],
                template.get('end_date'), bool(template.get('auto_confirm', False)),
                bool(template.get('active', True)), template.get('accounts_id_target'),
                int(tid),
            ))
            cur.execute("DELETE FROM Recurring_Template_Splits WHERE Templates_Id = %s", (int(tid),))
        else:
            cur.execute("""
                INSERT INTO Recurring_Templates
                    (Name, Accounts_Id, Payees_Id, Description, Total_Amount,
                     Periodicity, Next_Due_Date, End_Date, Auto_Confirm, Active, Accounts_Id_Target)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING Templates_Id
            """, (
                template['name'], template['accounts_id'], template.get('payees_id'),
                template.get('description'), template.get('total_amount'),
                template['periodicity'], template['next_due_date'],
                template.get('end_date'), bool(template.get('auto_confirm', False)),
                bool(template.get('active', True)), template.get('accounts_id_target'),
            ))
            tid = cur.fetchone()[0]

        if splits:
            execute_values(cur,
                "INSERT INTO Recurring_Template_Splits (Templates_Id, Categories_Id, Amount, Memo) VALUES %s",
                [(int(tid), s.get('categories_id'), s.get('amount'), s.get('memo') or None)
                 for s in splits]
            )
        conn.commit()
        return int(tid)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_recurring_template(templates_id: int):
    """Delete a template; nullifies any draft transactions that were generated from it."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE Transactions SET Templates_Id = NULL WHERE Templates_Id = %s AND Is_Draft = TRUE",
            (templates_id,)
        )
        cur.execute("DELETE FROM Recurring_Templates WHERE Templates_Id = %s", (templates_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def create_template_from_transaction(transaction_id: int) -> int:
    """Seed a new Recurring_Template from an existing confirmed transaction and its splits."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT Accounts_Id, Payees_Id, Description, Total_Amount, Accounts_Id_Target
            FROM Transactions WHERE Transactions_Id = %s
        """, (transaction_id,))
        tx = cur.fetchone()
        if not tx:
            raise ValueError(f"Transaction {transaction_id} not found")
        acc_id, payee_id, desc, amount, target_acc = tx

        cur.execute("""
            INSERT INTO Recurring_Templates
                (Name, Accounts_Id, Payees_Id, Description, Total_Amount,
                 Periodicity, Next_Due_Date, Accounts_Id_Target)
            VALUES (%s,%s,%s,%s,%s,'Monthly', CURRENT_DATE + INTERVAL '1 month', %s)
            RETURNING Templates_Id
        """, (desc or f"Template #{transaction_id}", acc_id, payee_id, desc, amount, target_acc))
        tid = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO Recurring_Template_Splits (Templates_Id, Categories_Id, Amount, Memo)
            SELECT %s, Categories_Id, Amount, Memo FROM Splits WHERE Transactions_Id = %s
        """, (tid, transaction_id))

        conn.commit()
        return int(tid)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def generate_draft_transactions() -> int:
    """Generate draft transactions for every active template whose Next_Due_Date <= today.

    Auto-confirm templates (installments etc.) are inserted as confirmed directly.
    Returns the count of transactions created.
    """
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        relativedelta = None

    from datetime import timedelta as _td

    if relativedelta is not None:
        _DELTAS = {
            'Daily':         lambda d: d + relativedelta(days=1),
            'Weekly':        lambda d: d + relativedelta(weeks=1),
            'Biweekly':      lambda d: d + relativedelta(weeks=2),
            'Monthly':       lambda d: d + relativedelta(months=1),
            'Quarterly':     lambda d: d + relativedelta(months=3),
            'Semiannually':  lambda d: d + relativedelta(months=6),
            'Annually':      lambda d: d + relativedelta(years=1),
        }
    else:
        _DELTAS = {
            'Daily':         lambda d: d + _td(days=1),
            'Weekly':        lambda d: d + _td(weeks=1),
            'Biweekly':      lambda d: d + _td(weeks=2),
            'Monthly':       lambda d: d + _td(days=30),
            'Quarterly':     lambda d: d + _td(days=91),
            'Semiannually':  lambda d: d + _td(days=183),
            'Annually':      lambda d: d + _td(days=365),
        }

    conn = get_connection()
    cur = conn.cursor()
    created = 0
    try:
        cur.execute("""
            SELECT Templates_Id, Accounts_Id, Payees_Id, Description, Total_Amount,
                   Periodicity, Next_Due_Date, End_Date, Auto_Confirm, Accounts_Id_Target
            FROM Recurring_Templates
            WHERE Active = TRUE
              AND Next_Due_Date <= CURRENT_DATE
              AND (End_Date IS NULL OR End_Date >= CURRENT_DATE)
        """)
        templates = cur.fetchall()

        for (tid, acc_id, payee_id, desc, amount, periodicity,
             next_due, end_date, auto_confirm, target_acc) in templates:

            cur.execute("""
                SELECT 1 FROM Transactions
                WHERE Templates_Id = %s AND Date = %s AND Is_Draft = TRUE
            """, (tid, next_due))
            if cur.fetchone():
                adv = _DELTAS.get(periodicity, _DELTAS['Monthly'])
                cur.execute(
                    "UPDATE Recurring_Templates SET Next_Due_Date = %s WHERE Templates_Id = %s",
                    (adv(next_due), tid)
                )
                continue

            is_draft = not bool(auto_confirm)

            cur.execute("""
                INSERT INTO Transactions
                    (Accounts_Id, Date, Payees_Id, Description, Total_Amount,
                     Is_Draft, Templates_Id, Cleared, Accounts_Id_Target)
                VALUES (%s,%s,%s,%s,%s,%s,%s,FALSE,%s)
                RETURNING Transactions_Id
            """, (acc_id, next_due, payee_id, desc, amount, is_draft, tid, target_acc))
            tx_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                SELECT %s, Categories_Id, Amount, Memo
                FROM Recurring_Template_Splits WHERE Templates_Id = %s
            """, (tx_id, tid))

            # For auto-confirmed transfer templates, create the mirror leg immediately
            if auto_confirm and target_acc:
                cur.execute("SELECT nextval('transfers_id_seq')")
                shared_tid = cur.fetchone()[0]
                mirror_amount = -float(amount or 0)
                cur.execute("""
                    INSERT INTO Transactions
                        (Accounts_Id, Date, Payees_Id, Description, Total_Amount,
                         Is_Draft, Cleared, Reconciled, Accounts_Id_Target, Transfers_Id)
                    VALUES (%s,%s,%s,%s,%s,FALSE,FALSE,FALSE,%s,%s)
                """, (target_acc, next_due, payee_id, desc, mirror_amount, acc_id, shared_tid))
                cur.execute(
                    "UPDATE Transactions SET Transfers_Id = %s WHERE Transactions_Id = %s",
                    (shared_tid, tx_id)
                )
                # Both legs carry Accounts_Id_Target, so the balance-maintaining
                # trigger (written for an older single-row transfer model)
                # double-applies the amount to each account when inserted
                # already-confirmed — recompute from scratch to correct it.
                for _acc in (acc_id, target_acc):
                    cur.execute("""
                        UPDATE Accounts
                           SET Accounts_Balance = COALESCE((
                               SELECT SUM(Total_Amount)
                               FROM Transactions
                               WHERE Accounts_Id = %s AND Is_Draft = FALSE
                           ), 0)
                         WHERE Accounts_Id = %s
                    """, (_acc, _acc))

            adv = _DELTAS.get(periodicity, _DELTAS['Monthly'])
            cur.execute(
                "UPDATE Recurring_Templates SET Next_Due_Date = %s WHERE Templates_Id = %s",
                (adv(next_due), tid)
            )
            created += 1

        conn.commit()
        return created
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_transaction_splits(transaction_id: int) -> pd.DataFrame:
    """Return splits for a single transaction with full recursive category path."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH RECURSIVE ch AS (
                    SELECT Categories_Id, Categories_Name::TEXT AS full_path
                    FROM   Categories
                    WHERE  Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                    FROM   Categories c
                    JOIN   ch ON c.Categories_Id_Parent = ch.Categories_Id
                )
                SELECT s.Splits_Id, s.Categories_Id,
                       ch.full_path AS categories_name, s.Amount, s.Memo
                FROM Splits s
                LEFT JOIN ch ON ch.Categories_Id = s.Categories_Id
                WHERE s.Transactions_Id = %s
                ORDER BY s.Splits_Id
            """, (transaction_id,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(
            columns=['splits_id', 'categories_id', 'categories_name', 'amount', 'memo']
        )
    finally:
        conn.close()


def save_draft_transaction(transaction_id: int, fields: dict, splits: list):
    """Update all editable fields of a draft transaction and replace its splits.

    fields keys: date, total_amount, description, accounts_id, payees_id,
                 accounts_id_target (optional)
    splits: list of dicts with keys categories_id, amount, memo
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE Transactions SET
                Date               = %s,
                Total_Amount       = %s,
                Description        = %s,
                Accounts_Id        = %s,
                Payees_Id          = %s,
                Accounts_Id_Target = %s
            WHERE Transactions_Id = %s AND Is_Draft = TRUE
        """, (
            fields['date'],
            fields['total_amount'],
            fields.get('description') or None,
            fields['accounts_id'],
            fields.get('payees_id') or None,
            fields.get('accounts_id_target') or None,
            transaction_id,
        ))
        # Replace splits
        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (transaction_id,))
        if splits:
            execute_values(cur,
                "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo) VALUES %s",
                [(transaction_id, s['categories_id'], s['amount'], s.get('memo') or None)
                 for s in splits if s.get('categories_id')]
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def confirm_draft_transaction(transaction_id: int):
    """Confirm a single draft — flips Is_Draft to FALSE which triggers balance update."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE Transactions SET Is_Draft = FALSE WHERE Transactions_Id = %s AND Is_Draft = TRUE",
            (transaction_id,)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_draft_transactions() -> pd.DataFrame:
    """Return all draft transactions with split summary and template name."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t.Transactions_Id,
                    t.Date,
                    t.Accounts_Id,
                    a.Accounts_Name,
                    t.Payees_Id,
                    p.Payees_Name,
                    t.Description,
                    t.Total_Amount,
                    t.Templates_Id,
                    rt.Name        AS Template_Name,
                    rt.Periodicity,
                    COALESCE(
                        STRING_AGG(
                            c.Categories_Name || ': ' || ROUND(s.Amount::numeric, 2)::text,
                            ' | '
                            ORDER BY s.Splits_Id
                        ),
                        '—'
                    ) AS Splits_Summary
                FROM Transactions t
                JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                LEFT JOIN Recurring_Templates rt ON rt.Templates_Id = t.Templates_Id
                LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                LEFT JOIN Categories c ON c.Categories_Id = s.Categories_Id
                WHERE t.Is_Draft = TRUE
                GROUP BY t.Transactions_Id, t.Date, t.Accounts_Id, a.Accounts_Name,
                         t.Payees_Id, p.Payees_Name, t.Description, t.Total_Amount,
                         t.Templates_Id, rt.Name, rt.Periodicity
                ORDER BY t.Date ASC
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(
            columns=['transactions_id','date','accounts_id','accounts_name','payees_id',
                     'payees_name','description','total_amount','templates_id',
                     'template_name','periodicity','splits_summary']
        )
    finally:
        conn.close()


def get_confirmed_from_templates() -> pd.DataFrame:
    """Return confirmed transactions that originated from a recurring template (history log)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t.Transactions_Id,
                    t.Date,
                    a.Accounts_Name,
                    p.Payees_Name,
                    t.Description,
                    t.Total_Amount,
                    rt.Name AS Template_Name,
                    rt.Periodicity
                FROM Transactions t
                JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                JOIN Recurring_Templates rt ON rt.Templates_Id = t.Templates_Id
                WHERE t.Is_Draft = FALSE AND t.Templates_Id IS NOT NULL
                ORDER BY t.Date DESC
                LIMIT 200
            """)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(
            columns=['transactions_id','date','accounts_name','payees_name',
                     'description','total_amount','template_name','periodicity']
        )
    finally:
        conn.close()


def save_nwr_account_selection(account_ids: list, settings_key: str = 'nwr_account_ids'):
    """Persist an account selection to app_settings under *settings_key*."""
    import json
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)
        """)
        cur.execute("""
            INSERT INTO app_settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (settings_key, json.dumps(account_ids)))
        conn.commit()
    finally:
        cur.close()
        conn.close()