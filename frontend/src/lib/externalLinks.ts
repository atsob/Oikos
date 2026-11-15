/** A security's page on Yahoo Finance. */
export function yahooUrl(ticker: string): string {
  return `https://finance.yahoo.com/quote/${encodeURIComponent(ticker)}`
}

/** TradingView's chart for EXCHANGE:SYMBOL — works for every symbol shape (U.UN, KIMBER/A, ESSITY_B); without an
 *  exchange, TradingView picks the main listing. */
export function tradingViewUrl(symbol: string, exchange?: string): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(exchange ? `${exchange}:${symbol}` : symbol)}`
}
