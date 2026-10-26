import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { useAccountInView } from "../accounts";
import { ApiError } from "../api/client";
import type { ChangeOut, QuantityConflict } from "../api/types";
import { PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import { afterChange, TRANSACTIONS_KEY, useAssets, useTransaction } from "../transactions";
import {
  ConflictDialog,
  DeleteTransactionDialog,
  useDescribe,
  useWarningTexts,
} from "./TransactionDialogs";
import { inputsOf, keptFields } from "./transactionDraft";
import { backOf, TransactionForm } from "./TransactionForm";

/**
 * Editing a transaction (D-141): the full form of mockup 05 with the stored values, the type
 * included, saved as one atomic edit (§4); "Delete…" opens the delete dialog of mockup 06.
 * Cancel, Save and Delete go back to the list as it was left: its filters come with the
 * link (`state.back`).
 */
export default function EditTransaction() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { accounts, search } = useAccountInView();
  const assets = useAssets();
  const tx = useTransaction(id);
  const describe = useDescribe();
  const warningTexts = useWarningTexts();
  const [dialog, setDialog] = useState<"delete" | QuantityConflict | null>(null);

  const back = backOf(location.state);
  const backSearch = back.back ?? search;
  const backPath = back.backPath ?? "/transactions";
  const title = <PageTitle title={t("editTransaction.heading")} />;

  if (tx.isError) {
    if (tx.error instanceof ApiError && tx.error.code === "NOT_FOUND") {
      return (
        <>
          {title}
          <section className="card empty-state">
            <p className="muted">{t("editTransaction.notFound")}</p>
            <Link to={{ pathname: backPath, search: backSearch }} className="button">
              {t("editTransaction.toList")}
            </Link>
          </section>
        </>
      );
    }
    return (
      <>
        {title}
        <LoadError error={tx.error} onRetry={() => void tx.refetch()} />
      </>
    );
  }
  if (assets.isError) {
    return (
      <>
        {title}
        <LoadError error={assets.error} onRetry={() => void assets.refetch()} />
      </>
    );
  }
  if (!tx.data || !assets.data || accounts.length === 0) {
    return (
      <>
        {title}
        <Loading />
      </>
    );
  }

  const stored = tx.data;
  const asset = assets.data.find((a) => a.id === stored.asset_id);
  const account = accounts.find((a) => a.id === stored.account_id);
  const deleted = (out: ChangeOut) => {
    // Gone: its own query must not ask for it again.
    client.removeQueries({ queryKey: [...TRANSACTIONS_KEY, "one", stored.id] });
    void afterChange(client);
    const text = [t("editTransaction.deleted"), ...warningTexts(out.warnings)].join(" ");
    navigate({ pathname: backPath, search: backSearch }, { state: { notice: text } });
  };

  return (
    <>
      {title}
      <p className="muted page-intro">
        {t("editTransaction.intro", {
          what: describe(stored, asset, account),
          account: account?.name ?? "",
        })}
      </p>
      <TransactionForm
        key={stored.id}
        initial={inputsOf(stored, i18n.language)}
        replaces={stored.id}
        keep={keptFields(stored)}
        back={backSearch}
        backPath={backPath}
        onDelete={() => setDialog("delete")}
      />
      {dialog === "delete" && (
        <DeleteTransactionDialog
          tx={stored}
          asset={asset}
          account={account}
          onClose={() => setDialog(null)}
          onDeleted={deleted}
          onConflict={setDialog}
        />
      )}
      {dialog !== null && dialog !== "delete" && (
        <ConflictDialog
          conflict={dialog}
          asset={asset}
          formLost
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}
