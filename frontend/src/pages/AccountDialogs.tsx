import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ACCOUNTS_KEY } from "../accounts";
import { api } from "../api/client";
import type { Account } from "../api/types";
import { CurrencySelect } from "../components/CurrencySelect";
import { Dialog } from "../components/Dialog";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";

type FormProps = {
  account?: Account; // absent: a new account
  defaultName?: string; // a new account: e.g. the account name of an imported file (D-142)
  defaultCurrency: string;
  onClose: () => void;
  onSaved: (account: Account) => void;
  onDelete?: () => void;
};

/**
 * Mockup 15, "Nuovo conto"; the same form edits an account. The currency is fixed once the
 * account has a transaction (D-39), so it is not sent then.
 */
export function AccountFormDialog({
  account,
  defaultName = "",
  defaultCurrency,
  onClose,
  onSaved,
  onDelete,
}: FormProps) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [name, setName] = useState(account?.name ?? defaultName);
  const [currency, setCurrency] = useState(account?.currency ?? defaultCurrency);
  const [provider, setProvider] = useState(account?.provider ?? "");
  const [code, setCode] = useState(account?.external_code ?? "");
  const [nameMissing, setNameMissing] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const locked = account?.currency_locked ?? false;

  const save = useMutation({
    mutationFn: () => {
      const fields = { name: name.trim(), provider, ...(locked ? {} : { currency }) };
      return account
        ? api<Account>("PATCH", `/accounts/${account.id}`, { ...fields, external_code: code })
        : api<Account>("POST", "/accounts", fields);
    },
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ACCOUNTS_KEY });
      onSaved(saved);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const missing = name.trim() === "";
    setNameMissing(missing);
    if (missing) nameRef.current?.focus();
    else save.mutate();
  };

  return (
    <Dialog
      title={t(account ? "accountForm.editHeading" : "accountForm.newHeading")}
      onClose={onClose}
    >
      <p className="muted">{t("accountForm.intro")}</p>
      <form onSubmit={submit} noValidate>
        <FormError error={save.error} />
        <Field
          label={t("accountForm.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
          autoComplete="off"
          error={nameMissing ? t("validation.required") : undefined}
          inputRef={nameRef}
          required
        />
        <CurrencySelect
          label={t("accountForm.currency")}
          value={currency}
          onChange={setCurrency}
          disabled={locked}
          hint={locked ? t("accountForm.currencyLocked") : undefined}
        />
        <Field
          label={t("accountForm.provider")}
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          maxLength={100}
          autoComplete="off"
        />
        {account && (
          <Field
            label={t("accountForm.externalCode")}
            hint={t("accountForm.externalCodeHint")}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            maxLength={64}
            autoComplete="off"
            spellCheck={false}
          />
        )}
        {!account && <p className="notice">{t("accountForm.currencyNote")}</p>}
        <div className="actions">
          {onDelete && (
            <button
              type="button"
              className="button button-danger-quiet push-left"
              onClick={onDelete}
            >
              {t("accountForm.delete")}
            </button>
          )}
          <button type="button" className="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            {t(account ? "accountForm.save" : "accountForm.create")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

type DeleteProps = {
  account: Account;
  onClose: () => void;
  onDeleted: (deletedTransactions: number) => void;
};

/** Mockup 15, "Eliminare il conto?": the account and its transactions, after typing its name
 * (D-77). Archiving is V2. */
export function DeleteAccountDialog({ account, onClose, onDeleted }: DeleteProps) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === account.name;

  const remove = useMutation({
    mutationFn: () =>
      api<{ deleted_transactions: number }>("DELETE", `/accounts/${account.id}`, {
        confirm_name: typed,
      }),
    onSuccess: (out) => {
      // Gone at once from every list; its cached summary simply expires.
      client.setQueryData<Account[]>(ACCOUNTS_KEY, (list) =>
        list?.filter((a) => a.id !== account.id),
      );
      onDeleted(out.deleted_transactions);
    },
  });

  return (
    <Dialog danger title={t("deleteAccount.heading", { name: account.name })} onClose={onClose}>
      <p className="muted">{t("deleteAccount.text", { count: account.transaction_count })}</p>
      <p className="alert">{t("deleteAccount.archive")}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (matches) remove.mutate();
        }}
        noValidate
      >
        <FormError error={remove.error} />
        <Field
          label={t("deleteAccount.confirm")}
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
            {t("deleteAccount.submit")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
