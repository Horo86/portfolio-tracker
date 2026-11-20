import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api } from "../api/client";
import type { MarketDataStatus, PriceRole } from "../api/types";
import { engineName } from "../format/engine";
import { useScreenTexts } from "../i18n";
import { PRICE_SOURCES_TEXTS } from "../priceSourcesTexts";
import { LoadError, Loading } from "./Status";

const ROLES: readonly PriceRole[] = ["ETF_STOCK", "BOND", "FX", "CLASSIFY"];
const CHECKS = ["OK", "NOT_FOUND", "MISMATCH", "FAILING"] as const;

/** Read only (D-175 (4)): whether market data is on and, per role, the enabled engines in
 * their configured order, each with the counts of its last searches (D-135: counts only).
 * Changing them is the price-sources page (D-196), linked at the end. */
export function PriceSourcesCard() {
  useScreenTexts(PRICE_SOURCES_TEXTS);
  const { t, i18n } = useTranslation();
  const headingId = useId();
  const status = useQuery({
    queryKey: ["admin", "market-data-status"],
    queryFn: ({ signal }) =>
      api<MarketDataStatus>("GET", "/admin/market-data/status", undefined, signal),
  });
  const data = status.data;
  const counts = new Map(data?.engines.map((engine) => [engine.name, engine.checks]));
  const number = new Intl.NumberFormat(i18n.language);

  const engineText = (name: string) => {
    const checks = counts.get(name) ?? {};
    const parts = CHECKS.filter((key) => checks[key]).map(
      (key) => `${number.format(checks[key])} ${t(`priceSources.checks.${key}`)}`,
    );
    const shown = engineName(name);
    return parts.length > 0 ? `${shown} (${parts.join(", ")})` : shown;
  };

  return (
    <section className="card price-sources" aria-labelledby={headingId}>
      <h2 id={headingId} className="card-label">
        {t("priceSources.heading")}
      </h2>
      {status.isPending && <Loading />}
      {status.isError && (
        <LoadError error={status.error} onRetry={() => void status.refetch()} level={3} />
      )}
      {data && (
        <>
          <p className="muted">
            {t(data.market_data === "ON" ? "priceSources.on" : "holdings.pricesOff")}
          </p>
          <dl className="rows">
            {ROLES.map((role) => {
              const engines = data.routes[role] ?? [];
              return (
                <div className="row" key={role}>
                  <dt>{t(`priceSources.roles.${role}`)}</dt>
                  <dd>
                    {engines.length > 0 ? (
                      <ol className="engine-order">
                        {engines.map((name) => (
                          <li key={name}>{engineText(name)}</li>
                        ))}
                      </ol>
                    ) : (
                      <span className="muted">
                        {t(role === "BOND" ? "priceSources.noBond" : "priceSources.none")}
                      </span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        </>
      )}
      <p className="price-sources-manage">
        <Link to="/admin/price-sources">{t("priceSources.manage")}</Link>
      </p>
    </section>
  );
}
