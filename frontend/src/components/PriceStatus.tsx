import { useTranslation } from "react-i18next";

import type { Holding, Me } from "../api/types";
import { formatDay } from "../format/date";
import { staleKey, statusCode, statusTone } from "../priceStatus";

const TONE_CLASS = { ok: "pill-ok", info: "pill-soft", warning: "pill-warning" } as const;

type Props = {
  holding: Holding;
  marketData: Me["market_data"];
  form?: "short" | "long"; // a pill in a table cell, or the sentence of the detail page
  showOk?: boolean; // "automatic prices" is said only where the status is the subject
  offAbove?: boolean; // the page says once that market data is off (the holdings list)
};

/** What a holding's price rests on: its status (unless OK), a stale close, a manual close,
 * an estimate at cost, a value missing for a rate (§12, D-46, D-64, D-144, D-145). */
export function PriceNotes(props: Props) {
  const { holding, marketData, form = "short", showOk = false, offAbove = false } = props;
  const { t, i18n } = useTranslation();
  const code = statusCode(holding, marketData);
  const stale = staleKey(holding, code);
  const notes: { text: string; className: string }[] = [];
  if (code && (code !== "OK" || showOk) && !(code === "OFF" && offAbove)) {
    notes.push({ text: t(`priceStatus.${form}.${code}`), className: TONE_CLASS[statusTone(code)] });
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
