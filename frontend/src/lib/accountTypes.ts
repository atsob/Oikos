// Shared Accounts_Type groupings, used across several pages/modals for filtering
// dropdowns. Kept out of any page component file — a page file that exports both
// its default component and a plain constant breaks Vite Fast Refresh (the export
// is "incompatible", so editing that file forces a full reload instead of a fast
// in-place update) — see StaticData.tsx/Investments.tsx/Register.tsx history.

// Bank/cash-like account types a transaction can be posted against (Cash Register,
// New Transaction modal).
export const CASH_ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Other']

// Investment account types (Inv. Portfolio, Recurring/Investment transfer pickers).
export const INVESTMENT_ACCOUNT_TYPES = ['Brokerage', 'Pension', 'Other Investment', 'Margin']

// Bank/cash-like account types eligible as an investment account's Linked Account
// (its cash settlement account) — deliberately narrower than the broader
// CASH_ACCOUNT_TYPES above (which also includes Loan/Real Estate/Vehicle/Asset —
// none of which make sense as a linked settlement account for an investment).
export const LINKABLE_ACCOUNT_TYPES = ['Cash', 'Checking', 'Savings', 'Credit Card']

// Account types eligible as a Loan account's Linked Asset Account (the thing the
// loan financed — a house, a car, etc.), Static Data -> Accounts.
export const LOAN_LINKABLE_ASSET_TYPES = ['Real Estate', 'Vehicle', 'Asset']

// Loan Type options, Static Data -> Accounts (matches Quicken's own list).
export const LOAN_TYPES = [
  'Mortgage', 'Loan', 'Auto Loan', 'Consumer Loan', 'Commercial Loan',
  'Student Loan', 'Military Loan', 'Business Loan', 'Construction Loan', 'Home Equity Loan',
]
