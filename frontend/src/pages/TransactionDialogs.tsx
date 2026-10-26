import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useAccountInView } from "../accounts";
import { api } from "../api/client";
import type {
  Account,
  Asset,
  ChangeOut,
  LedgerWarning,
  QuantityConflict,
  Transaction,
  TransactionType,
} from "../api/types";
import { Dialog } from "../components/Dialog";
import { FormError } from "../components/FormError";
import { formatDay } from "../format/date";
import { formatMoney, formatQuantity } from "../format/decimal";
import { conflictOf, usePreview, useTransaction } from "../transactions";

/** The texts of the warnings a change introduces (D-139: the others were there before). */
export function useWarningTexts() {
  const { t, i18n } = useTranslation();
  const { accounts } = useAccountInView();
  return (warnings: LedgerWarning[]) =>
    warnings
      .filter((w) => w.introduced)
      .map((w) =>
        t(`warnings.${w.code}`, {
          date: formatDay(w.date, i18n.language),
          account: accounts.find((a) => a.id === w.account_id)?.name ?? "",
        }),
      );
}

const WITH_QUANTITY: ReadonlySet<TransactionType> = new Set(["BUY", "SELL"]);

/** A stored transaction in words: "Sell of 6 Mondo Azionario ETF", "Deposit of €2,000.00". */
export function useDescribe() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  return (tx: Transaction, asset?: Asset, account?: Account): string => {
    const type = t(`types.${tx.type}`);
    if (!tx.asset_id) {
      const amount = formatMoney(tx.total_amount ?? "0", account?.currency ?? "", locale);
      return t("describe.amount", { type, amount });
    }
    const name = asset?.name ?? t("conflict.thisAsset");
    if (WITH_QUANTITY.has(tx.type) && tx.quantity) {
      const quantity = formatQuantity(tx.quantity, locale);
      return t("describe.quantity", { type, quantity, asset: name });
    }
    return t("describe.asset", { type, asset: name });
  };
}

type Own = { type: TransactionType; date: string; quantity?: string };

type ConflictProps = {
  conflict: QuantityConflict;
  own?: Own; // the transaction of the form, when the conflict is its own
  ownId?: string; // the id of the transaction the form edits
  asset?: Asset;
  formLost?: boolean; // a form with the user's input is behind the dialog
  onClose: () => void;
};

/**
 * Mockup 06, "Questa modifica non si può salvare": a change refused because a quantity would
 * become negative (§4, 409 QUANTITY_CONFLICT). It names the first transaction in conflict and
 * what was held just before it (`held`, D-139), with the ways to proceed.
 */
export function ConflictDialog({
  conflict,
  own,
  ownId,
  asset,
  formLost = false,
  onClose,
}: ConflictProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  // The form's own transaction is described as typed, not as stored.
  const otherId = own && conflict.transaction_id === ownId ? undefined : conflict.transaction_id;
  const other = useTransaction(otherId);

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
  // Screen 10 for what was held before, dated before the first transaction in conflict.
  const held = new URLSearchParams({
    account: conflict.account_id,
    asset: conflict.asset_id,
    ...(first?.date ? { before: first.date } : {}),
  });

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
        <li>
          {t("conflict.addHeld")}{" "}
          <Link to={{ pathname: "/holdings/existing", search: `?${held}` }}>
            {t("conflict.addHeldLink")}
          </Link>
          {first?.date && (
            <span className="block small">
              {t("conflict.addHeldBefore", { date: formatDay(first.date, locale) })}
            </span>
          )}
          {formLost && <span className="block small">{t("conflict.formLost")}</span>}
        </li>
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

// A cash change of zero, as the API writes it ("0", "0.00", "-0.00").
const ZERO = /^-?0(\.0+)?$/;

type DeleteProps = {
  tx: Transaction;
  asset?: Asset;
  account?: Account;
  onClose: () => void;
  onDeleted: (out: ChangeOut) => void;
  onConflict: (conflict: QuantityConflict) => void;
};

/**
 * Mockup 06, "Eliminare questa transazione?": what goes, and what the server's dry run of the
 * delete says follows (D-139): the quantity or the cash after it, the warnings it introduces
 * and a quantity conflict, which a press on Delete then explains in the conflict dialog.
 */
export function DeleteTransactionDialog({
  tx,
  asset,
  account,
  onClose,
  onDeleted,
  onConflict,
}: DeleteProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const describe = useDescribe();
  const warningTexts = useWarningTexts();
  const preview = usePreview({ replaces: tx.id });
  const remove = useMutation({
    mutationFn: () => api<ChangeOut>("DELETE", `/transactions/${tx.id}`),
    onSuccess: onDeleted,
    onError: (error) => {
      const conflict = conflictOf(error);
      if (conflict) onConflict(conflict);
    },
  });

  const shown = preview.current ? preview.data : undefined;
  const conflict = preview.current ? conflictOf(preview.error) : null;
  const after: string[] = [];
  if (shown) {
    const moves = tx.type === "BUY" || tx.type === "SELL" || tx.type === "STOCK_SPLIT";
    if (shown.position && moves) {
      after.push(
        t("deleteTransaction.quantityAfter", {
          asset: asset?.name ?? t("conflict.thisAsset"),
          quantity: formatQuantity(shown.position.quantity_after, locale),
        }),
      );
    }
    if (!ZERO.test(shown.cash.change)) {
      after.push(
        t("deleteTransaction.cashAfter", {
          account: account?.name ?? "",
          cash: formatMoney(shown.cash.after, shown.currency, locale),
        }),
      );
    }
  }
  const warnings = shown ? warningTexts(shown.warnings) : [];
  const failed = remove.error && !conflictOf(remove.error) ? remove.error : null;

  return (
    <Dialog danger title={t("deleteTransaction.heading")} onClose={onClose}>
      <p className="muted">
        {t("deleteTransaction.text", {
          what: describe(tx, asset, account),
          date: formatDay(tx.trade_date, locale),
          account: account?.name ?? "",
        })}
      </p>
      <div role="status">
        {after.length > 0 && (
          <div className="notice-warning">
            <p>{t("deleteTransaction.after")}</p>
            <ul>
              {after.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </div>
        )}
        {warnings.length > 0 && (
          <ul className="warnings">
            {warnings.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        )}
        {conflict && (
          <p className="alert">
            {t("summary.conflictOther", {
              held: formatQuantity(conflict.held, locale),
              after: formatQuantity(conflict.quantity, locale),
            })}
          </p>
        )}
      </div>
      <FormError error={failed} />
      <div className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
        >
          {t("deleteTransaction.submit")}
        </button>
      </div>
    </Dialog>
  );
}
