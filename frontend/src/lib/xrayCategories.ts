// Manual Style Box options for Portfolio X-Ray's Style Box view — for a fund Morningstar's own Style Box (fetched by ISIN, see
// data/morningstar.py) does not cover. The same 3x3 grids Morningstar uses: size by style for equity funds, credit quality by
// interest-rate sensitivity for bond funds; plus catch-alls for funds with no grid (commodities, allocation funds).
export const STYLE_BOX_CATEGORIES = [
  'Large Value', 'Large Blend', 'Large Growth',
  'Mid-Cap Value', 'Mid-Cap Blend', 'Mid-Cap Growth',
  'Small Value', 'Small Blend', 'Small Growth',
  'High Quality / Limited Duration', 'High Quality / Moderate Duration', 'High Quality / Extensive Duration',
  'Medium Quality / Limited Duration', 'Medium Quality / Moderate Duration', 'Medium Quality / Extensive Duration',
  'Low Quality / Limited Duration', 'Low Quality / Moderate Duration', 'Low Quality / Extensive Duration',
  'Money Market', 'Commodities', 'Allocation', 'Other',
]

// Manual asset-class override options for X-Ray's Asset Allocation view — for
// funds Yahoo's generic 6-bucket asset_classes split can't classify correctly
// (e.g. a physical commodity ETC lands 100% in Yahoo's "other" bucket, since
// Yahoo has no commodity-specific asset class). When set, the fund's whole
// value routes to this one class instead of being split across Yahoo's buckets.
export const ASSET_CLASS_OVERRIDE_OPTIONS = [
  'Stocks', 'Bonds', 'Cash', 'Commodities', 'Crypto', 'Preferred', 'Convertible', 'Other',
]
