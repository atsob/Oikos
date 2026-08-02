// Manual category-override options for Portfolio X-Ray's Style Box view —
// used when Yahoo doesn't supply a Morningstar-style category (mostly
// non-US ETFs). Standard Morningstar equity style box + common bond
// categories + catch-alls.
export const STYLE_BOX_CATEGORIES = [
  'Large Value', 'Large Blend', 'Large Growth',
  'Mid-Cap Value', 'Mid-Cap Blend', 'Mid-Cap Growth',
  'Small Value', 'Small Blend', 'Small Growth',
  'Short-Term Bond', 'Intermediate Core Bond', 'Intermediate Core-Plus Bond', 'High Yield Bond', 'Long-Term Bond', 'World Bond',
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
