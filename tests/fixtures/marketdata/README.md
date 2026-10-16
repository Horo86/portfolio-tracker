# Market data fixtures (synthetic)

Every file here is **invented**: rates, prices, symbols, names and ISINs (country code `ZZ`,
which belongs to no country) are fictitious. Only the *shape* follows the providers'
documented responses, so the adapters can be tested without the network.

- `frankfurter_*`: Frankfurter API v2 `GET /v2/rates?providers=ecb&base=EUR&quotes=…&from=…&to=…`
  (a JSON array of `{date, base, quote, rate}`; errors are `{status, message}`), shape checked
  against https://frankfurter.dev in October 2026. `frankfurter_v1_shape.json` is the older
  v1 shape, used to test that a changed format is detected.
- `yahoo_*`: Yahoo Finance (unofficial, undocumented) `GET /v1/finance/search?q=…` and
  `GET /v8/finance/chart/{symbol}?interval=1d&…`. Key names, types, the `GBp` currency code
  of London listings, timestamps at the exchange's opening time and HTTP 404 for an unknown
  symbol were checked against public responses in October 2026. The *body* of the 404
  (`yahoo_chart_not_found.json`) and the `BOND` instrument type are assumed, not observed.
  Closes such as `98.73999786376953` reproduce Yahoo's float32 serialisation.
