import { useMutation } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { Asset, SearchResult } from "../api/types";
import { groupResults, type SearchRow } from "../assetSearch";
import { exchangeName } from "../format/exchange";
import { useMe } from "../session";
import { type AssetDraft, AssetCreateForm } from "./AssetCreateForm";
import { Field } from "./Field";
import { FormError } from "./FormError";

const MIN_QUERY = 2;
const MAX_QUERY = 40; // D-128, as the server checks it

type Props = {
  currency: string; // "your currency": the account's in view, else the reporting currency
  onCreated: (asset: Asset) => void;
  onCancel: () => void;
  existing?: (asset: Asset) => ReactNode; // what to offer for a security the user already has
  headingLevel?: 2 | 3;
};

/**
 * Mockup 11 (D-148): the search of the price source, by ISIN (automatic prices) or by name
 * or symbol (manual prices only), and the create-by-hand card under it. "Aggiungi" on a
 * result only fills the card in: nothing is stored until "Crea titolo". On the page of
 * screen 11 and in the dialog of screen 10.
 */
export function AddAsset({ currency, onCreated, onCancel, existing, headingLevel = 2 }: Props) {
  const { t } = useTranslation();
  const off = useMe().data?.market_data === "OFF";
  const [text, setText] = useState("");
  const [tooShort, setTooShort] = useState(false);
  // The draft the create card starts from; `serial` makes a new one start afresh.
  const [draft, setDraft] = useState<{ serial: number; values: AssetDraft; fromResult: boolean }>(
    { serial: 0, values: {}, fromResult: false },
  );
  const resultsId = useId();
  const createId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";

  const search = useMutation({
    mutationFn: (q: string) => api<SearchResult[]>("POST", "/assets/search", { q }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (off || search.isPending) return;
    const q = text.trim();
    if (q.length < MIN_QUERY || q.length > MAX_QUERY) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    search.mutate(q);
  };

  const fill = (values: AssetDraft) =>
    setDraft(({ serial }) => ({ serial: serial + 1, values, fromResult: true }));

  // While the box asks for 2 to 40 characters, the last results would not be of what it says.
  const rows = search.data && !tooShort ? groupResults(search.data, (r) => exchangeName(t, r.exchange_code, r.exchange)) : null;
  const isinRows = rows?.filter((row) => row.isin) ?? [];
  // Offered only when no ISIN result is in your currency (C1): otherwise it is noise.
  const offer =
    isinRows.length > 0 && !isinRows.some((row) => row.currency === currency) ? isinRows[0] : null;

  return (
    <>
      <section className="card form-section" aria-labelledby={resultsId}>
        <Heading id={resultsId} className="visually-hidden">
          {t("assetSearch.searchHeading")}
        </Heading>
        {off && <p className="notice">{t("errors.MARKET_DATA_OFF")}</p>}
        <form role="search" className="search-form" onSubmit={submit} noValidate>
          <Field
            label={t("assetSearch.label")}
            hint={t("assetSearch.hint")}
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={MAX_QUERY}
            autoComplete="off"
            spellCheck={false}
            disabled={off}
            error={tooShort ? t("assetSearch.length") : undefined}
          />
          <button type="submit" className="button button-primary" disabled={off}>
            {t("assetSearch.button")}
          </button>
        </form>
        <FormError error={search.error} />
        <div role="status" aria-busy={search.isPending}>
          {search.isPending && <p className="muted">{t("assetSearch.searching")}</p>}
          {!search.isPending && rows && (
            <p className="muted small">
              {rows.length === 0 ? t("assetSearch.none") : t("assetSearch.count", { count: rows.length })}
            </p>
          )}
        </div>
        {!search.isPending && rows && rows.length > 0 && (
          <ul className="search-results">
            {rows.map((row) => (
              <ResultRow key={row.key} row={row} onAdd={() => fill(draftOf(row))} />
            ))}
          </ul>
        )}
        {!search.isPending && offer && (
          <p className="search-offer">
            <span>{t("assetSearch.otherCurrency", { currency })}</span>
            <button
              type="button"
              className="button"
              onClick={() =>
                fill({
                  ...draftOf(offer),
                  quote_currency: currency,
                  quote_unit: offer.quote_unit === "PERCENT_OF_NOMINAL" ? offer.quote_unit : "CURRENCY",
                })
              }
            >
              {t("assetSearch.createIn", { currency })}
            </button>
          </p>
        )}
      </section>

      <section className="card form-section" aria-labelledby={createId}>
        <Heading id={createId}>{t("assetSearch.createHeading")}</Heading>
        <p className="muted">{t("assetSearch.createText")}</p>
        <AssetCreateForm
          key={draft.serial}
          defaultCurrency={currency}
          initial={draft.values}
          nameHint={draft.fromResult ? t("assetSearch.checkName") : undefined}
          focusName={draft.fromResult}
          existing={existing}
          onCancel={onCancel}
          onCreated={(asset) => {
            setDraft(({ serial }) => ({ serial: serial + 1, values: {}, fromResult: false }));
            onCreated(asset);
          }}
        />
      </section>
    </>
  );
}

/** What "Aggiungi" puts into the create card: an ISIN result's ISIN, a text result's symbol
 * as the ticker; the name stays editable, a missing type must be chosen (C3). */
function draftOf(row: SearchRow): AssetDraft {
  return {
    name: row.name,
    asset_type: row.asset_type,
    quote_currency: row.currency,
    quote_unit: row.quote_unit,
    isin: row.isin,
    ticker: row.ticker,
  };
}

function ResultRow({ row, onAdd }: { row: SearchRow; onAdd: () => void }) {
  const { t } = useTranslation();
  const name = row.name || t("assetSearch.unnamed");
  const meta = t("assetSearch.meta", {
    type: row.asset_type ? t(`assetTypes.${row.asset_type}`) : t("assetSearch.noType"),
    code: row.isin ?? row.ticker,
    currency: row.currency,
  });
  return (
    <li className="search-result">
      <div>
        <strong className="block">{name}</strong>
        <span className="muted small block">{meta}</span>
        {row.exchanges.length > 0 && (
          <span className="muted small block">
            {t("assetSearch.exchanges", { list: row.exchanges.join(", ") })}
          </span>
        )}
        {!row.isin && <span className="small block">{t("assetSearch.manualOnly")}</span>}
      </div>
      <button
        type="button"
        className="button button-soft"
        aria-label={t("assetSearch.addLabel", { name, currency: row.currency })}
        onClick={onAdd}
      >
        {t("assetSearch.add")}
      </button>
    </li>
  );
}
