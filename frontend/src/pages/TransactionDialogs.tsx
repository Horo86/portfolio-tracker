import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { Asset, QuantityConflict, Transaction, TransactionType } from "../api/types";
import { Dialog } from "../components/Dialog";
import { formatDay } from "../format/date";
import { formatQuantity } from "../format/decimal";
import { TRANSACTIONS_KEY } from "../transactions";

type Own = { type: TransactionType; date: string; quantity?: string };

type ConflictProps = {
  conflict: QuantityConflict;
  own: Own; // the transaction of the form, when the conflict is its own (index 0)
  asset?: Asset;
  onClose: () => void;
};

/**
 * Mockup 06, "Questa modifica non si può salvare": a change refused because a quantity would
 * become negative (§4, 409 QUANTITY_CONFLICT). It names the first transaction in conflict and
 * what was held just before it (`held`, D-139), with the ways to proceed.
 */
export function ConflictDialog({ conflict, own, asset, onClose }: ConflictProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const otherId = conflict.transaction_id;
  const other = useQuery({
    queryKey: [...TRANSACTIONS_KEY, "one", otherId],
    queryFn: ({ signal }) => api<Transaction>("GET", `/transactions/${otherId}`, undefined, signal),
    enabled: otherId !== undefined,
  });

  const first: Own | undefined =
    otherId === undefined
      ? own
      : other.data && {
          type: other.data.type,
          date: other.data.trade_date,
          quantity: other.data.quantity ?? undefined,
        };
  const values = {
    asset: asset?.name ?? t("conflict.thisAsset"),
    after: formatQuantity(conflict.quantity, locale),
    held: formatQuantity(conflict.held, locale),
  };

  return (
    <Dialog danger title={t("conflict.heading")} onClose={onClose}>
      <p className="muted">
        {t(conflict.reason === "SPLIT_TO_ZERO" ? "conflict.splitText" : "conflict.text", values)}
      </p>
      {first && (
        <p className="alert">
          {t(first.quantity ? "conflict.first" : "conflict.firstNoQuantity", {
            ...values,
            type: t(`types.${first.type}`),
            quantity: first.quantity ? formatQuantity(first.quantity, locale) : "",
            date: first.date ? formatDay(first.date, locale) : "",
          })}
        </p>
      )}
      <h3 className="dialog-subheading">{t("conflict.howTo")}</h3>
      <ul className="dialog-list">
        <li>{t("conflict.fixQuantity")}</li>
        <li>{t("conflict.addHeld")}</li>
        <li>{t("conflict.editTogether")}</li>
      </ul>
      <div className="actions">
        <button type="button" className="button button-primary" onClick={onClose}>
          {t("conflict.back")}
        </button>
      </div>
    </Dialog>
  );
}
