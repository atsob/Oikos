// Manual category-override options for Portfolio X-Ray's Style Box view —
// used when Yahoo doesn't supply a Morningstar-style category (mostly
// non-US ETFs). Standard Morningstar equity style box + bond categories
// (incl. money market and inflation-linked) + regional and sector equity
// categories + catch-alls. The category is free text to the Style Box, which
// simply groups funds by it, so adding an option here needs nothing else.
export const STYLE_BOX_CATEGORIES = [
  'Large Value', 'Large Blend', 'Large Growth',
  'Mid-Cap Value', 'Mid-Cap Blend', 'Mid-Cap Growth',
  'Small Value', 'Small Blend', 'Small Growth',
  'Money Market', 'Ultrashort Bond', 'Short-Term Bond', 'Intermediate Core Bond', 'Intermediate Core-Plus Bond', 'Long-Term Bond',
  'High Yield Bond', 'Inflation-Linked Bond', 'Corporate Bond', 'Government Bond', 'World Bond', 'Emerging Markets Bond',
  // Regional equity — Morningstar's own categories are mostly regional, not just the US style box
  'Global Stock', 'Europe Stock', 'Eurozone Stock', 'Emerging Markets Stock', 'China Region Stock', 'Latin America Stock', 'Asia Pacific Stock',
  // Sector / thematic equity
  'Technology', 'Health', 'Financial', 'Communications', 'Consumer Cyclical', 'Consumer Defensive', 'Industrials', 'Utilities', 'Real Estate',
  'Equity Energy', 'Natural Resources', 'Equity Precious Metals', 'Infrastructure', 'Miscellaneous Sector',
  'Commodities', 'Allocation', 'Other',
]

// Manual asset-class override options for X-Ray's Asset Allocation view — for
// funds Yahoo's generic 6-bucket asset_classes split can't classify correctly
// (e.g. a physical commodity ETC lands 100% in Yahoo's "other" bucket, since
// Yahoo has no commodity-specific asset class). When set, the fund's whole
// value routes to this one class instead of being split across Yahoo's buckets.
export const ASSET_CLASS_OVERRIDE_OPTIONS = [
  'Stocks', 'Bonds', 'Cash', 'Commodities', 'Crypto', 'Preferred', 'Convertible', 'Other',
]
