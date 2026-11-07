import { useTranslation } from "react-i18next";

import type { Holding, Me } from "../api/types";
import { formatDay } from "../format/date";
import { exchangeName } from "../format/exchange";
import { staleKey, type StatusCode, statusCode, statusTone } from "../priceStatus";

const TONE_CLASS = { ok: "pill-ok", info: "pill-soft", warning: "pill-warning" } as const;

// The sentences that name the exchange and the latest close, when the holding has one from a
// provider (Obs 4, D-171): automatic prices, and the two that keep the older prices in use.
const WITH_LATEST_CLOSE: ReadonlySet<StatusCode> = new Set(["OK", "FAILING", "NOT_LISTED"]);

type Props = {
  holding: Holding;
  marketData: Me["market_data"];
  form?: "short" | "long"; // a pill in a table cell, or the sentence of the detail page
  showOk?: boolean; // "automatic prices" is said only where the status is the subject
  offAbove?: boolean; // the page says once that market data is off (the holdings list)
};

/** The exchange, then the listing's symbol in brackets when known: "Milan (VNGA80.MI)". */
function withSymbol(exchange: string, holding: Holding): string {
  return holding.price_symbol ? `${exchange} (${holding.price_symbol})` : exchange;
}

/** What a holding's price rests on: its status (unless OK), a stale close, a manual close,
 * an estimate at cost, a value missing for a rate (§12, D-46, D-64, D-144, D-145). */
export function PriceNotes(props: Props) {
  const { holding, marketData, form = "short", showOk = false, offAbove = false } = props;
  const { t, i18n } = useTranslation();
  const code = statusCode(holding, marketData);
  const stale = staleKey(holding, code);
  const notes: { text: string; className: string }[] = [];
  if (code && (code !== "OK" || showOk) && !(code === "OFF" && offAbove)) {
    const { price_source: source, price_exchange: exchange, price_date: day } = holding;
    const latest =
      form === "long" && WITH_LATEST_CLOSE.has(code) && source === "PROVIDER" && exchange && day;
    const text = latest
      ? t(`priceStatus.longWithClose.${code}`, {
          exchange: withSymbol(exchangeName(t, holding.price_exchange_code, exchange), holding),
          date: formatDay(day, i18n.language),
        })
      : t(`priceStatus.${form}.${code}`);
    notes.push({ text, className: TONE_CLASS[statusTone(code)] });
  }
  if (stale && holding.price_date) {
    const date = formatDay(holding.price_date, i18n.language);
    notes.push({ text: t(stale, { date }), className: "pill-warning" });
  }
  if (holding.price_source === "MANUAL" && code !== "MANUAL") {
    notes.push({ text: t("priceStatus.manual"), className: "pill-soft" });
  }
  if (holding.estimated_at_cost) {
    notes.push({ text: t("priceStatus.estimatedAtCost"), className: "pill-soft" });
  }
  if (holding.value_unavailable) {
    notes.push({ text: t("priceStatus.valueUnavailable"), className: "pill-warning" });
  }
  if (notes.length === 0) return null;
  return (
    <ul className={`price-notes price-notes-${form}`}>
      {notes.map((note) => (
        <li key={note.text} className={`pill ${note.className}`}>
          {note.text}
        </li>
      ))}
    </ul>
  );
}
