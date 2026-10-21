import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { Account, Asset, ChangeOut, QuantityConflict, Transaction } from "../api/types";
import { Dialog } from "../components/Dialog";
import { Field, SelectField } from "../components/Field";
import { FormError } from "../components/FormError";
import { conflictOf } from "../transactions";
import { layoutOf } from "./transactionDraft";

/** What the bulk edit of mockup 06 can change (D-141); "" means no change. */
export type BulkChoice = {
  account: string;
  noCash: "" | "yes" | "no"; // "do not move cash"
  notes: string; // replaces the notes; empty: no change
};

export type TransactionPatch = {
  id: string;
  account_id?: string;
  affects_cash?: boolean;
  notes?: string;
};

/** Whether the type has "do not move cash" (domain-rules §2). */
const hasCashFlag = (tx: Transaction) => layoutOf(tx.type).cashOption;

/** One patch per transaction the choice really changes; the cash flag only where the type
 * has it. */
export function bulkEdits(txs: readonly Transaction[], choice: BulkChoice): TransactionPatch[] {
  const notes = choice.notes.trim();
  return txs.flatMap((tx) => {
    const patch: TransactionPatch = { id: tx.id };
    if (choice.account && choice.account !== tx.account_id) patch.account_id = choice.account;
    if (choice.noCash && hasCashFlag(tx)) {
      const affects = choice.noCash === "no";
      if (affects !== tx.affects_cash) patch.affects_cash = affects;
    }
    if (notes && notes !== tx.notes) patch.notes = notes;
    return Object.keys(patch).length > 1 ? [patch] : [];
  });
}

/** The one currency of the transactions' accounts, or null when they differ. */
function commonCurrency(txs: readonly Transaction[], accounts: readonly Account[]) {
  const currencies = new Set(
    txs.map((tx) => accounts.find((a) => a.id === tx.account_id)?.currency),
  );
  const [only] = currencies;
  return currencies.size === 1 && only ? only : null;
}

type Done = { out: ChangeOut; count: number };

type EditProps = {
  txs: readonly Transaction[];
  accounts: readonly Account[];
  onClose: () => void;
  onSaved: (done: Done) => void;
  onConflict: (conflict: QuantityConflict) => void;
};

/**
 * Mockup 06, "Modifica N transazioni insieme" (D-141): the account (same currency only,
 * D-98), "do not move cash" and the notes, saved as one change checked as a whole (D-06b).
 * A refusal opens the conflict dialog on top, so the choices are still here after it.
 */
export function BulkEditDialog({ txs, accounts, onClose, onSaved, onConflict }: EditProps) {
  const { t } = useTranslation();
  const notesId = useId();
  const [choice, setChoice] = useState<BulkChoice>({ account: "", noCash: "", notes: "" });
  const [nothing, setNothing] = useState(false);
  const currency = commonCurrency(txs, accounts);
  const targets = currency ? accounts.filter((a) => a.currency === currency) : [];
  const flagged = txs.filter(hasCashFlag).length;

  const save = useMutation({
    mutationFn: (edits: TransactionPatch[]) =>
      api<ChangeOut>("POST", "/transactions/bulk-edit", { edits }),
    onSuccess: (out, edits) => onSaved({ out, count: edits.length }),
    onError: (error) => {
      const conflict = conflictOf(error);
      if (conflict) onConflict(conflict);
    },
  });

  const change = (update: Partial<BulkChoice>) => {
    setChoice((current) => ({ ...current, ...update }));
    setNothing(false);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    // Not disabled while saving: a disabled button loses the focus, which the conflict
    // dialog on top must give back to it.
    if (save.isPending) return;
    const edits = bulkEdits(txs, choice);
    if (edits.length === 0) setNothing(true);
    else save.mutate(edits);
  };

  let cashHint: string | undefined;
  if (flagged === 0) cashHint = t("bulkEdit.cashNone");
  else if (flagged < txs.length) {
    cashHint = t("bulkEdit.cashSome", { count: flagged, total: txs.length });
  }
  const failed = save.error && !conflictOf(save.error) ? save.error : null;

  return (
    <Dialog title={t("bulkEdit.heading", { count: txs.length })} onClose={onClose}>
      <p className="muted">{t("bulkEdit.text")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={failed} />
        <SelectField
          label={t("bulkEdit.account")}
          hint={currency ? undefined : t("bulkEdit.accountMixed")}
          value={choice.account}
          disabled={!currency}
          onChange={(e) => change({ account: e.target.value })}
        >
          <option value="">{t("bulkEdit.noChange")}</option>
          {targets.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name} ({account.currency})
            </option>
          ))}
        </SelectField>
        <SelectField
          label={t("bulkEdit.noCash")}
          hint={cashHint}
          value={choice.noCash}
          disabled={flagged === 0}
          onChange={(e) => change({ noCash: e.target.value as BulkChoice["noCash"] })}
        >
          <option value="">{t("bulkEdit.noChange")}</option>
          <option value="yes">{t("bulkEdit.yes")}</option>
          <option value="no">{t("bulkEdit.no")}</option>
        </SelectField>
        <div className="field">
          <label htmlFor={notesId}>{t("bulkEdit.notes")}</label>
          <textarea
            id={notesId}
            aria-describedby={`${notesId}-hint`}
            value={choice.notes}
            onChange={(e) => change({ notes: e.target.value })}
            maxLength={2000}
            rows={3}
          />
          <span id={`${notesId}-hint`} className="field-hint">
            {t("bulkEdit.notesHint")}
          </span>
        </div>
        {nothing && (
          <p className="alert" role="alert">
            {t("bulkEdit.nothing")}
          </p>
        )}
        <div className="actions">
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="button button-primary">
            {t("bulkEdit.submit", { count: txs.length })}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

type DeleteProps = {
  ids: readonly string[];
  onClose: () => void;
  onDeleted: (done: Done) => void;
  onConflict: (conflict: QuantityConflict) => void;
};

/** The selected transactions deleted together (D-06b): a sale and the buy it needs can go
 * at once. A refusal opens the conflict dialog on top. */
export function BulkDeleteDialog({ ids, onClose, onDeleted, onConflict }: DeleteProps) {
  const { t } = useTranslation();
  const remove = useMutation({
    mutationFn: () => api<ChangeOut>("POST", "/transactions/bulk-delete", { ids }),
    onSuccess: (out) => onDeleted({ out, count: out.deleted.length }),
    onError: (error) => {
      const conflict = conflictOf(error);
      if (conflict) onConflict(conflict);
    },
  });
  const failed = remove.error && !conflictOf(remove.error) ? remove.error : null;

  return (
    <Dialog danger title={t("bulkDelete.heading", { count: ids.length })} onClose={onClose}>
      <p className="muted">{t("bulkDelete.text")}</p>
      <FormError error={failed} />
      <div className="actions">
        <button type="button" className="button" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button
          type="button"
          className="button button-danger"
          onClick={() => {
            // Not disabled while deleting: the conflict dialog gives the focus back here.
            if (!remove.isPending) remove.mutate();
          }}
        >
          {t("bulkDelete.submit", { count: ids.length })}
        </button>
      </div>
    </Dialog>
  );
}

type DeleteAllProps = {
  account: Account;
  asset: Asset;
  count: number;
  onClose: () => void;
  onDeleted: (done: Done) => void;
};

/**
 * Mockup 06, "Eliminare tutte le transazioni di un titolo?" (D-06b, D-141): every
 * transaction of the security in the account, to start its history again. Always possible,
 * since no other security depends on them; the security's name is typed to confirm.
 */
export function DeleteAllDialog({ account, asset, count, onClose, onDeleted }: DeleteAllProps) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === asset.name;
  const remove = useMutation({
    mutationFn: () =>
      api<ChangeOut>("DELETE", `/accounts/${account.id}/assets/${asset.id}/transactions`),
    onSuccess: (out) => onDeleted({ out, count: out.deleted.length }),
  });

  return (
    <Dialog danger title={t("deleteAll.heading")} onClose={onClose}>
      <p className="muted">
        {t("deleteAll.text", { asset: asset.name, account: account.name, count })}
      </p>
      <p className="alert">{t("deleteAll.what")}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (matches) remove.mutate();
        }}
        noValidate
      >
        <FormError error={remove.error} />
        <Field
          label={t("deleteAll.confirm")}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <div className="actions">
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="submit"
            className="button button-danger"
            disabled={!matches || remove.isPending}
          >
            {t("deleteAll.submit", { count })}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
