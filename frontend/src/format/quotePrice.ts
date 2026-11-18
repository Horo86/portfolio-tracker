import type { TFunction } from "i18next";

import type { Holding } from "../api/types";
import { formatPrice } from "./decimal";

/** A price in the asset's quote unit: currency, its hundredths, or % of nominal (§2). */
export function quotePrice(
  t: TFunction,
  holding: Pick<Holding, "quote_unit" | "quote_currency">,
  value: string,
  locale: string,
) {
  if (holding.quote_unit === "PERCENT_OF_NOMINAL") {
    return t("summary.percent", { value: formatPrice(value, locale) });
  }
  if (holding.quote_unit === "MINOR_CURRENCY_UNIT") {
    return t("holdings.minorUnit", {
      value: formatPrice(value, locale),
      currency: holding.quote_currency,
    });
  }
  return formatPrice(value, locale, holding.quote_currency);
}
