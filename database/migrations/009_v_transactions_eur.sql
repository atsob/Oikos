-- Migration 009: AI helper view — transactions with amounts converted to EUR
-- Used by the AI assistant agent so it can query amount_eur directly
-- without having to reason about multi-currency FX joins.

CREATE OR REPLACE VIEW v_transactions_eur AS
SELECT
    t.transactions_id,
    t.date,
    t.description,
    t.total_amount,
    c.currencies_shortname                                             AS currency,
    COALESCE(
        (SELECT fx.fx_rate FROM historical_fx fx
         WHERE fx.currencies_id_1 = a.currencies_id
         ORDER BY fx.date DESC LIMIT 1), 1
    )                                                                  AS fx_rate,
    t.total_amount * COALESCE(
        (SELECT fx.fx_rate FROM historical_fx fx
         WHERE fx.currencies_id_1 = a.currencies_id
         ORDER BY fx.date DESC LIMIT 1), 1
    )                                                                  AS amount_eur,
    t.payees_id,
    p.payees_name                                                      AS payee,
    a.accounts_id,
    a.accounts_name,
    a.accounts_type,
    t.accounts_id_target
FROM transactions t
JOIN accounts a    ON a.accounts_id    = t.accounts_id
JOIN currencies c  ON c.currencies_id  = a.currencies_id
LEFT JOIN payees p ON p.payees_id      = t.payees_id;
