import { useTranslation } from "react-i18next";

import { formatMoney, formatPercent, signOf } from "../format/decimal";

export const TONE = { "-1": "negative", "0": "", "1": "positive" } as const;
const tone = (value: string | null) => (value === null ? "" : TONE[signOf(value)]);

/** The gain figures of a holding or of a whole view: the same names in both API answers. */
export type Gains = {
  unrealized: string | null;
  unrealized_pct: string | null;
  realized_gross: string;
  realized_net: string;
  realized_pct_gross: string | null;
  realized_pct_net: string | null;
  income_gross: string;
  income_net: string;
  gain_gross: string | null;
  gain_net: string | null;
  taxes: string;
  realized_taxes: string;
  income_taxes: string;
};

type Props = {
  gains: Gains;
  currency: string;
  incomeLabel: string; // dividends, coupons, or both
  headingId: string;
  partial?: boolean; // the total leaves out what could not be valued (D-64)
};

/** "Da dove viene il guadagno" (mockups 01, 03): unrealized and realized with their %, income
 * and the total in money only (D-41), gross and net, and the taxes withheld split (D-12). */
export function GainsCard({ gains, currency, incomeLabel, headingId, partial = false }: Props) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const money = (value: string) => formatMoney(value, currency, locale, { signed: true });
  const plain = (value: string) => formatMoney(value, currency, locale);
  const percent = (value: string) => formatPercent(value, locale, { signed: true });
  const { unrealized, gain_gross, gain_net } = gains;

  return (
    <section className="card" aria-labelledby={headingId}>
      <h2 id={headingId}>{t("holding.gainsHeading")}</h2>
      <p className="muted small">{t("holding.gainsIntro")}</p>
      <table className="data gain-table">
        <thead>
          <tr>
            <th scope="col">{t("holding.item")}</th>
            <th scope="col" className="num">
              {t("holding.gross")}
            </th>
            <th scope="col" className="num">
              {t("holding.net")}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">
              {t("holding.unrealized")}
              {gains.unrealized_pct !== null && (
                <span className="muted small block">
                  {t("holding.unrealizedPct", { value: percent(gains.unrealized_pct) })}
                </span>
              )}
            </th>
            <td className={`num ${tone(unrealized)}`}>
              {unrealized === null ? "—" : money(unrealized)}
            </td>
            <td className={`num ${tone(unrealized)}`}>
              {unrealized === null ? "—" : money(unrealized)}
            </td>
          </tr>
          <tr>
            <th scope="row">
              {t("holding.realized")}
              {gains.realized_pct_gross !== null && gains.realized_pct_net !== null && (
                <span className="muted small block">
                  {t("holding.realizedPct", {
                    gross: percent(gains.realized_pct_gross),
                    net: percent(gains.realized_pct_net),
                  })}
                </span>
              )}
            </th>
            <td className={`num ${tone(gains.realized_gross)}`}>{money(gains.realized_gross)}</td>
            <td className={`num ${tone(gains.realized_net)}`}>{money(gains.realized_net)}</td>
          </tr>
          <tr>
            <th scope="row">
              {incomeLabel}
              <span className="muted small block">{t("holding.incomeNoPct")}</span>
            </th>
            <td className={`num ${tone(gains.income_gross)}`}>{money(gains.income_gross)}</td>
            <td className={`num ${tone(gains.income_net)}`}>{money(gains.income_net)}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">
              {t("holding.total")}
              {partial && (
                <span className="pill pill-warning block-pill">{t("accounts.partial")}</span>
              )}
            </th>
            <td className={`num ${tone(gain_gross)}`}>
              {gain_gross === null ? "—" : money(gain_gross)}
            </td>
            <td className={`num strong ${tone(gain_net)}`}>
              {gain_net === null ? "—" : money(gain_net)}
            </td>
          </tr>
        </tfoot>
      </table>
      <p className="muted small">
        {t("holding.taxesLine", {
          taxes: plain(gains.taxes),
          realized: plain(gains.realized_taxes),
          income: plain(gains.income_taxes),
        })}
      </p>
    </section>
  );
}
