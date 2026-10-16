# Market data fixtures (synthetic)

Every file here is **invented**: rates, prices, symbols, names and ISINs (country code `ZZ`,
which belongs to no country) are fictitious. Only the *shape* follows the providers'
documented responses, so the adapters can be tested without the network.

- `frankfurter_*`: Frankfurter API v2 `GET /v2/rates?providers=ecb&base=EUR&quotes=…&from=…&to=…`
  (a JSON array of `{date, base, quote, rate}`; errors are `{status, message}`), shape checked
  against https://frankfurter.dev in October 2026. `frankfurter_v1_shape.json` is the older
  v1 shape, used to test that a changed format is detected.
