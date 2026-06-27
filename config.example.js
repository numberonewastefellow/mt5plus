// Copy this file to config.js and fill in your free API keys.
//
//   TwelveData (primary): https://twelvedata.com/pricing  (free tier, no credit card)
//     - Used for XAU/USD (gold) by default.
//     - Free tier does NOT include XAG/USD (silver) — fallback handles it.
//
//   Finnhub (fallback):   https://finnhub.io/register  (free tier, no credit card)
//     - Used for XAG/USD (silver) always (TwelveData free does not support it).
//     - Also used as automatic fallback for gold when TwelveData errors or is rate-limited.
//     - Free tier: 60 calls/minute, way more headroom than TwelveData.
window.APP_CONFIG = {
  twelveDataApiKey: 'YOUR_TD_KEY_HERE',
  finnhubApiKey: 'YOUR_FH_KEY_HERE'
};
