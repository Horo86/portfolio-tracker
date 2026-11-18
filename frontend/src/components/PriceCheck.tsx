import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import type { AssetType, Me, PriceCheckResult, QuoteUnit } from "../api/types";
import { errorText } from "../errors";
import { formatDay } from "../format/date";
import { engineName } from "../format/engine";
import { exchangeName } from "../format/exchange";
import { quotePrice } from "../format/quotePrice";
import { ME_KEY } from "../session";

type Props = {
  isin: string; // normalized
  currency: string;
  unit: QuoteUnit;
  type: AssetType | null;
  ready: boolean; // a valid ISIN, and a type, currency and unit chosen
};

type Answer = { key: string; result?: PriceCheckResult; error?: unknown };

/**
 * "Controlla la fonte prezzi" (D-178, D-179): on the user's click only, never while typing
 * (D-128, D-148), the source, exchange and latest price the security would be expected to
 * get. Information only: it never fills a field and never holds the save back. The answer is
 * shown only beside the values it was asked for, and is cleared when one of them changes.
 */
export function PriceCheck({ isin, currency, unit, type, ready }: Props) {
  const { t, i18n } = useTranslation();
  // From the session's cache only: the app loads /me before any page (no request here).
  const me = useQuery<Me | null>({ queryKey: ME_KEY, enabled: false });
  const key = JSON.stringify([isin, currency, unit, type]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  // Any change of the values clears the answer, so an old one never comes back beside them.
  const [valuesSeen, setValuesSeen] = useState(key);
  if (valuesSeen !== key) {
    setValuesSeen(key);
    setAnswer(null);
  }
  const [running, setRunning] = useState<string | null>(null); // the key being checked
  const busy = useRef(false); // set before any render: a second click in the same moment
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  if (me.data?.market_data === "OFF") return null;

  const run = async () => {
    if (!ready || type === null || busy.current) return;
    busy.current = true;
    const asked = key;
    setRunning(asked);
    setAnswer(null);
    let done: Answer;
    try {
      const result = await api<PriceCheckResult>("POST", "/assets/price-check", {
        isin,
        quote_currency: currency,
        quote_unit: unit,
        asset_type: type,
      });
      done = { key: asked, result };
    } catch (error) {
      done = { key: asked, error };
    }
    busy.current = false;
    if (!mounted.current) return;
    setRunning(null);
    setAnswer(done);
  };

  const shown = answer?.key === key ? answer : null;
  const locale = i18n.language;
  return (
    <div className="price-check">
      <button
        type="button"
        className="button"
        disabled={!ready || running !== null}
        onClick={() => void run()}
      >
        {running !== null ? t("priceCheck.checking") : t("priceCheck.button")}
      </button>
      <div role="status" className="price-check-result">
        {running === key && <span className="visually-hidden">{t("priceCheck.checking")}</span>}
        {shown?.result && <ResultText t={t} result={shown.result} locale={locale} />}
        {shown && shown.error !== undefined && <p>{checkErrorText(t, shown.error)}</p>}
      </div>
    </div>
  );
}

function ResultText({ t, result, locale }: { t: TFunction; result: PriceCheckResult; locale: string }) {
  if (result.outcome === "FOUND") {
    const source = engineName(result.source ?? "");
    const exchange = exchangeName(t, result.exchange_code, result.exchange ?? "");
    const text =
      result.price !== null && result.price_date !== null
        ? t("priceCheck.found", {
            source,
            exchange,
            price: quotePrice(t, { quote_unit: result.quote_unit, quote_currency: result.currency }, result.price, locale),
            date: formatDay(result.price_date, locale),
          })
        : t("priceCheck.foundNoPrice", { source, exchange });
    return (
      <>
        <p>{text}</p>
        <p className="muted small">{t("priceCheck.foundNote")}</p>
      </>
    );
  }
  if (result.outcome === "NO_SOURCE") {
    const why = result.reason === "NO_ENGINE" ? "priceCheck.noEngine" : "priceCheck.notFound";
    return (
      <p>
        {t("priceCheck.noSource")} {t(why)}
      </p>
    );
  }
  return <p>{t(result.reason === "UNIT" ? "priceCheck.mismatchUnit" : "priceCheck.mismatchCurrency")}</p>;
}

/** The check's own texts for its refusals; any other error as the app words it. */
export function checkErrorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError) {
    const wait = error.retryAfter === null ? null : Math.max(1, error.retryAfter);
    if (error.code === "TOO_MANY_ATTEMPTS" && wait !== null) {
      return t("priceCheck.tooMany", { count: wait, seconds: wait });
    }
    if (error.code === "MARKET_DATA_BUSY" && wait !== null) {
      return t("priceCheck.busy", { count: wait, seconds: wait });
    }
    if (error.code === "MARKET_DATA_UNAVAILABLE") return t("priceCheck.unavailable");
  }
  return errorText(t, error);
}
