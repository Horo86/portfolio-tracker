import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { useAccountInView } from "../accounts";
import type { Asset } from "../api/types";
import { AddAsset } from "../components/AddAsset";
import { PageTitle } from "../components/PageTitle";
import { useMe } from "../session";

/** The texts after a security is created: what happens to its prices (D-145, D-148). */
export function addedTexts(asset: Asset, marketData: "ON" | "OFF" | undefined) {
  if (!asset.isin) return "assetSearch.addedManual";
  return marketData === "OFF" ? "assetSearch.addedOff" : "assetSearch.addedLooking";
}

/** The pages that start from a security just created, with the account in view kept. */
export function nextLinks(asset: Asset, accountId: string | null) {
  const params = (extra: Record<string, string>) =>
    `?${new URLSearchParams({ ...(accountId ? { account: accountId } : {}), ...extra })}`;
  return {
    existing: { pathname: "/holdings/existing", search: params({ asset: asset.id }) },
    transaction: { pathname: "/transactions/new", search: params({ asset: asset.id }) },
  };
}

/** Mockup 11, "Aggiungi un titolo" (D-148): search by ISIN or text, or create by hand. */
export default function AssetSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const me = useMe().data;
  const { account, search } = useAccountInView();
  const [created, setCreated] = useState<{ asset: Asset; serial: number } | null>(null);
  const doneRef = useRef<HTMLDivElement>(null);
  const currency = account?.currency ?? me?.reporting_currency ?? "EUR";
  const links = created && nextLinks(created.asset, account?.id ?? null);

  return (
    <>
      <PageTitle title={t("assetSearch.title")} />
      <p className="muted page-intro">{t("assetSearch.intro")}</p>

      {/* Built when shown, in the language of the moment. */}
      <div role="status" ref={doneRef} tabIndex={-1}>
        {created && links && (
          <div className="notice added-notice" key={created.serial}>
            <p>
              {t("assetSearch.added", { name: created.asset.name })}{" "}
              {t(addedTexts(created.asset, me?.market_data))}
            </p>
            <p className="next-links">
              <Link to={links.existing}>{t("assetSearch.addHoldings")}</Link>
              <Link to={links.transaction}>{t("assetSearch.addTransaction")}</Link>
            </p>
          </div>
        )}
      </div>

      <AddAsset
        currency={currency}
        onCancel={() => navigate({ pathname: "/holdings", search })}
        existing={(asset) => (
          <Link to={{ pathname: `/holdings/${asset.id}`, search }}>{t("assetSearch.open")}</Link>
        )}
        onCreated={(asset) => {
          setCreated((last) => ({ asset, serial: (last?.serial ?? 0) + 1 }));
          doneRef.current?.focus();
        }}
      />
    </>
  );
}
