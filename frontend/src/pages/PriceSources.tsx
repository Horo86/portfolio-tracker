import { type QueryKey, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { type FormEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { api, ApiError } from "../api/client";
import type {
  MarketDataRun,
  MarketDataRunStatus,
  PriceEngine,
  PriceEngines,
  PriceRole,
  PriceRoutes,
} from "../api/types";
import { Dialog } from "../components/Dialog";
import { Field } from "../components/Field";
import { FormError } from "../components/FormError";
import { Notice, useNotice } from "../components/Notice";
import { PageTitle } from "../components/PageTitle";
import { LoadError, Loading } from "../components/Status";
import { Body, Head, Table, Td, Th, Tr } from "../components/Table";
import { errorText } from "../errors";
import { formatMoment } from "../format/date";
import { engineName } from "../format/engine";
import { exchangeName } from "../format/exchange";
import { useScreenTexts } from "../i18n";
import { exchangeProblem, leftEmpty, moved } from "../priceSources";
import { PRICE_SOURCES_TEXTS } from "../priceSourcesTexts";
import { useMe } from "../session";
import NotFound from "./NotFound";

const STATUS_KEY = ["admin", "market-data-status"] as const; // shared with the card
const ENGINES_KEY = ["admin", "market-data-engines"] as const;
const ROUTES_KEY = ["admin", "market-data-routes"] as const;
const CHECKS = ["OK", "NOT_FOUND", "MISMATCH", "FAILING"] as const;
const POLL_MS = 3000; // the status while a run is in progress, only while this page is open
const WATCH_MS = 60_000; // after "Aggiorna ora", how long a queued run is waited for

/**
 * Screen 17's price sources (D-196): the administrator turns engines on and off, orders them
 * per role, sets the preferred exchanges and starts an update. Each change is one write that
 * the API audits and that queues the re-check (D-186, D-188); last write wins; no step-up
 * (D-187). Counts, codes and names only: never an ISIN, a symbol or a credential (D-135).
 * A non-administrator gets "not found", as from the API (D-92).
 */
export default function PriceSources() {
  const me = useMe().data;
  if (me?.role !== "ADMINISTRATOR") return <NotFound />;
  return <Sources />;
}

type Area = "engines" | "exchanges" | `role:${PriceRole}`;
type Confirm = { roles: PriceRole[]; run: () => void };

function Sources() {
  useScreenTexts(PRICE_SOURCES_TEXTS);
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const client = useQueryClient();
  const { notice, announce } = useNotice();
  const [watchSince, setWatchSince] = useState<number | null>(null);

  const status = useQuery({
    queryKey: STATUS_KEY,
    queryFn: ({ signal }) =>
      api<MarketDataRunStatus>("GET", "/admin/market-data/status", undefined, signal),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.running) return POLL_MS;
      if (watchSince === null || Date.now() - watchSince > WATCH_MS) return false;
      const started = data?.last_run?.started_at;
      return started && Date.parse(started) >= watchSince ? false : POLL_MS;
    },
  });
  const engines = useQuery({
    queryKey: ENGINES_KEY,
    queryFn: ({ signal }) =>
      api<PriceEngines>("GET", "/admin/market-data/engines", undefined, signal),
  });
  const routes = useQuery({
    queryKey: ROUTES_KEY,
    queryFn: ({ signal }) => api<PriceRoutes>("GET", "/admin/market-data/routes", undefined, signal),
  });

  // A run that ends changes the engines' counts and health: read them again.
  const running = status.data?.running ?? null;
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current !== null && running === null) {
      void client.invalidateQueries({ queryKey: ENGINES_KEY });
    }
    wasRunning.current = running;
  }, [running, client]);

  // One write at a time on the whole page (set before any render: a double click).
  const busy = useRef(false);
  const [pending, setPending] = useState<Area | null>(null);
  const [errors, setErrors] = useState<Partial<Record<Area, unknown>>>({});
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Where the focus goes once a change is drawn: the first of these controls that exists.
  const focusNext = useRef<string[] | null>(null);
  useLayoutEffect(() => {
    const wanted = focusNext.current;
    if (wanted === null) return;
    focusNext.current = null;
    const controls = [...document.querySelectorAll<HTMLElement>("[data-focus]")];
    for (const key of wanted) {
      const found = controls.find((element) => element.dataset.focus === key);
      if (found) {
        found.focus();
        return;
      }
    }
  }, [routes.data, engines.data]);

  async function write<T>(
    area: Area,
    request: () => Promise<T>,
    done: (out: T) => void,
    focus: string[] | null = null,
  ) {
    if (busy.current) return;
    busy.current = true;
    setPending(area);
    setErrors((now) => ({ ...now, [area]: null }));
    try {
      const out = await request();
      if (!mounted.current) return;
      focusNext.current = focus;
      done(out);
    } catch (error) {
      if (mounted.current) setErrors((now) => ({ ...now, [area]: error }));
    } finally {
      busy.current = false;
      if (mounted.current) setPending(null);
      for (const key of [STATUS_KEY, ENGINES_KEY, ROUTES_KEY] as QueryKey[]) {
        void client.invalidateQueries({ queryKey: key });
      }
    }
  }

  /** Runs `run` at once, or after the confirmation when the change would leave a guarded
   * role without an enabled engine (D-188 (6), D-196). */
  const guarded = (roles: PriceRole[], run: () => void) => {
    if (busy.current) return;
    if (roles.length === 0) run();
    else setConfirm({ roles, run });
  };

  const list = engines.data?.engines ?? [];
  const enabled = new Set(list.filter((engine) => engine.enabled).map((engine) => engine.name));
  const known = new Set(list.map((engine) => engine.name));
  const roleName = (role: PriceRole) => t(`priceSources.roles.${role}`, { defaultValue: role });

  const toggle = (engine: PriceEngine, on: boolean) => {
    const name = engineName(engine.name);
    const next = new Set(enabled);
    if (on) next.add(engine.name);
    else next.delete(engine.name);
    const emptied = on || !routes.data ? [] : leftEmpty(routes.data.routes, enabled, { enabled: next });
    guarded(emptied, () =>
      void write(
        "engines",
        () => api<PriceEngine>("PATCH", `/admin/market-data/engines/${encodeURIComponent(engine.name)}`, { enabled: on }),
        (out) => {
          client.setQueryData<PriceEngines>(ENGINES_KEY, (now) =>
            now && { ...now, engines: now.engines.map((e) => (e.name === out.name ? out : e)) },
          );
          announce(t(out.enabled ? "priceSources.page.turnedOn" : "priceSources.page.turnedOff", { name }));
        },
      ),
    );
  };

  const changeOrder = (role: PriceRole, order: string[], said: string, focus: string[]) => {
    const emptied = routes.data
      ? leftEmpty(routes.data.routes, enabled, { orders: { [role]: order } })
      : [];
    guarded(emptied, () =>
      void write(
        `role:${role}`,
        () => api<PriceRoutes>("PUT", `/admin/market-data/routes/${role}`, { engines: order }),
        (out) => {
          client.setQueryData(ROUTES_KEY, out);
          announce(said);
        },
        focus,
      ),
    );
  };

  const changeExchanges = (next: string[], said: string, focus: string[] | null, done?: () => void) =>
    write(
      "exchanges",
      () => api<PriceRoutes>("PUT", "/admin/market-data/exchanges", { exchanges: next }),
      (out) => {
        client.setQueryData(ROUTES_KEY, out);
        announce(said);
        done?.();
      },
      focus,
    );

  const off = (status.data ?? engines.data ?? routes.data)?.market_data === "OFF";

  return (
    <>
      <Link to="/admin" className="back-link">
        {t("priceSources.page.back")}
      </Link>
      <div className="page-head">
        <div>
          <PageTitle title={t("priceSources.heading")} />
          <p className="muted">{t("priceSources.page.intro")}</p>
        </div>
      </div>
      {off && <p className="notice-warning">{t("priceSources.page.offNotice")}</p>}
      <Notice notice={notice} />

      <RunCard
        status={status}
        off={off}
        locale={locale}
        onQueued={() => setWatchSince(Date.now())}
      />

      <section className="card ps-card" aria-labelledby="ps-engines">
        <h2 id="ps-engines" className="card-label">
          {t("priceSources.page.engines")}
        </h2>
        {engines.isPending && <Loading />}
        {engines.isError && (
          <LoadError error={engines.error} onRetry={() => void engines.refetch()} level={3} />
        )}
        <FormError error={errors.engines} />
        {engines.data && (
          <div className="table-scroll">
            <Table cards className="data engines-table" aria-labelledby="ps-engines">
              <Head>
                <Tr>
                  <Th scope="col">{t("priceSources.page.source")}</Th>
                  <Th scope="col">{t("priceSources.page.uses")}</Th>
                  <Th scope="col">{t("priceSources.page.on")}</Th>
                  <Th scope="col">{t("priceSources.page.status")}</Th>
                  <Th scope="col">{t("priceSources.page.lastSuccess")}</Th>
                  <Th scope="col">{t("priceSources.page.lastError")}</Th>
                  <Th scope="col">{t("priceSources.page.searches")}</Th>
                </Tr>
              </Head>
              <Body>
                {list.map((engine) => (
                  <EngineRow
                    key={engine.name}
                    engine={engine}
                    locale={locale}
                    busy={pending !== null}
                    roleName={roleName}
                    onToggle={(on) => toggle(engine, on)}
                  />
                ))}
              </Body>
            </Table>
          </div>
        )}
      </section>

      <section className="card ps-card" aria-labelledby="ps-orders">
        <h2 id="ps-orders" className="card-label">
          {t("priceSources.page.orders")}
        </h2>
        <p className="muted">{t("priceSources.page.ordersNote")}</p>
        {routes.isPending && <Loading />}
        {routes.isError && (
          <LoadError error={routes.error} onRetry={() => void routes.refetch()} level={3} />
        )}
        {routes.data &&
          (Object.keys(routes.data.routes) as PriceRole[]).map((role) => (
            <RoleOrder
              key={role}
              role={role}
              title={roleName(role)}
              order={routes.data.routes[role] ?? []}
              available={routes.data.available[role] ?? []}
              enabled={enabled}
              known={known}
              busy={pending !== null}
              error={errors[`role:${role}`]}
              onChange={(order, said, focus) => changeOrder(role, order, said, focus)}
            />
          ))}
      </section>

      <section className="card ps-card" aria-labelledby="ps-exchanges">
        <h2 id="ps-exchanges" className="card-label">
          {t("priceSources.page.exchanges")}
        </h2>
        <p className="muted">{t("priceSources.page.exchangesNote")}</p>
        {routes.isPending && <Loading />}
        {routes.data && (
          <Exchanges
            exchanges={routes.data.exchanges}
            busy={pending !== null}
            error={errors.exchanges}
            onChange={changeExchanges}
          />
        )}
      </section>

      <p className="muted ps-next">{t("priceSources.page.nextUpdate")}</p>

      {confirm && (
        <Dialog title={t("priceSources.page.confirmHeading")} onClose={() => setConfirm(null)}>
          <p>{t("priceSources.page.confirmText")}</p>
          <ul className="change-list">
            {confirm.roles.map((role) => (
              <li key={role}>{t(`priceSources.page.confirmRoles.${role}`)}</li>
            ))}
          </ul>
          <p className="muted">{t("priceSources.page.confirmNothingDeleted")}</p>
          <div className="actions">
            <button type="button" className="button" onClick={() => setConfirm(null)}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                const run = confirm.run;
                setConfirm(null);
                run();
              }}
            >
              {t("priceSources.page.confirmLeave")}
            </button>
          </div>
        </Dialog>
      )}
    </>
  );
}

/** The run's state and "Aggiorna ora" (D-121): its answer, the 409s included, is said in a
 * status region beside the button. */
function RunCard({
  status,
  off,
  locale,
  onQueued,
}: {
  status: UseQueryResult<MarketDataRunStatus>;
  off: boolean;
  locale: string;
  onQueued: () => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const { notice, announce } = useNotice();
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const data = status.data;

  const run = async () => {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    try {
      const out = await api<MarketDataRun>("POST", "/admin/market-data/run");
      onQueued();
      announce(t(out.queued === "QUEUED" ? "priceSources.page.runQueued" : "priceSources.page.runAlreadyQueued"));
    } catch (error) {
      announce(runErrorText(t, error));
    } finally {
      sending.current = false;
      setBusy(false);
      void client.invalidateQueries({ queryKey: STATUS_KEY });
    }
  };

  const last = data?.last_run ?? null;
  return (
    <section className="card ps-card ps-run" aria-labelledby="ps-run">
      <h2 id="ps-run" className="card-label">
        {t("priceSources.page.updates")}
      </h2>
      {status.isPending && <Loading />}
      {status.isError && (
        <LoadError error={status.error} onRetry={() => void status.refetch()} level={3} />
      )}
      {data && (
        <dl className="rows">
          <div className="row">
            <dt>{t("priceSources.page.automatic")}</dt>
            <dd>{t(data.market_data === "ON" ? "priceSources.page.automaticOn" : "priceSources.page.automaticOff")}</dd>
          </div>
          <div className="row">
            <dt>{t("priceSources.page.lastUpdate")}</dt>
            <dd>
              {data.running
                ? t("priceSources.page.runningNow")
                : last
                  ? t("priceSources.page.lastRun", {
                      result: t(`priceSources.page.results.${last.result}`, { defaultValue: last.result }),
                      when: formatMoment(last.finished_at ?? last.started_at, locale),
                    })
                  : t("priceSources.page.noRunYet")}
            </dd>
          </div>
          {data.next_run_at && (
            <div className="row">
              <dt>{t("priceSources.page.nextRun")}</dt>
              <dd>{formatMoment(data.next_run_at, locale)}</dd>
            </div>
          )}
          {data.recheck_pending && (
            <div className="row">
              <dt>{t("priceSources.page.recheck")}</dt>
              <dd>{t("priceSources.page.recheckPending")}</dd>
            </div>
          )}
        </dl>
      )}
      {data && !off && (
        <div className="actions actions-start ps-run-actions">
          <button
            type="button"
            className="button button-primary"
            aria-disabled={busy || undefined}
            onClick={() => void run()}
          >
            {t("priceSources.page.runNow")}
          </button>
        </div>
      )}
      <Notice notice={notice} />
    </section>
  );
}

function runErrorText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.code === "MARKET_DATA_RUNNING") return t("priceSources.page.runRunning");
  if (error instanceof ApiError && error.code === "MARKET_DATA_OFF") return t("priceSources.page.runOff");
  return errorText(t, error);
}

function EngineRow({
  engine,
  locale,
  busy,
  roleName,
  onToggle,
}: {
  engine: PriceEngine;
  locale: string;
  busy: boolean;
  roleName: (role: PriceRole) => string;
  onToggle: (on: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const name = engineName(engine.name);
  const number = new Intl.NumberFormat(i18n.language);
  const counts = CHECKS.filter((key) => engine.checks[key]).map(
    (key) => `${number.format(engine.checks[key])} ${t(`priceSources.checks.${key}`)}`,
  );
  return (
    <Tr>
      <Th scope="row" wide>
        <span className="engine-cell">
          <span className="engine-name">{name}</span>
          {!engine.official && (
            <span className="pill pill-warning">{t("priceSources.page.unofficial")}</span>
          )}
          {engine.terms_url && (
            <a className="small" href={engine.terms_url} rel="noreferrer">
              {t("priceSources.page.terms", { name })}
            </a>
          )}
        </span>
      </Th>
      <Td label={t("priceSources.page.uses")}>{engine.roles.map(roleName).join(", ")}</Td>
      <Td label={t("priceSources.page.on")}>
        <label className="tick switch-tick">
          <input
            type="checkbox"
            role="switch"
            checked={engine.enabled}
            aria-label={t("priceSources.page.useEngine", { name })}
            aria-disabled={busy || undefined}
            onChange={(e) => {
              if (!busy) onToggle(e.target.checked);
            }}
          />
          <span aria-hidden="true">{t("priceSources.page.use")}</span>
        </label>
      </Td>
      <Td label={t("priceSources.page.status")}>
        <EngineStatusText engine={engine} locale={locale} />
      </Td>
      <Td label={t("priceSources.page.lastSuccess")}>
        {engine.last_success_at ? (
          formatMoment(engine.last_success_at, locale)
        ) : (
          <span className="muted">{t("priceSources.page.sinceStart")}</span>
        )}
      </Td>
      <Td label={t("priceSources.page.lastError")}>
        {engine.last_error_code ? (
          <>
            {t(`priceSources.page.errorCodes.${engine.last_error_code}`, {
              defaultValue: engine.last_error_code,
            })}
            {engine.last_error_at && (
              <span className="muted small ps-when"> {formatMoment(engine.last_error_at, locale)}</span>
            )}
          </>
        ) : (
          <span className="muted">{t("priceSources.page.noError")}</span>
        )}
      </Td>
      <Td label={t("priceSources.page.searches")}>
        {counts.length > 0 ? counts.join(", ") : <span className="muted">{t("priceSources.none")}</span>}
      </Td>
    </Tr>
  );
}

/** D-188 (5): the status only next to an enabled engine; "off" otherwise. */
function EngineStatusText({ engine, locale }: { engine: PriceEngine; locale: string }) {
  const { t } = useTranslation();
  if (engine.off_by_environment) return <span className="muted">{t("priceSources.page.offByEnvironment")}</span>;
  if (!engine.enabled) return <span className="muted">{t("priceSources.page.off")}</span>;
  if (engine.status === "WORKING") {
    return <span className="pill pill-ok">{t("priceSources.page.statuses.WORKING")}</span>;
  }
  const text =
    engine.status === "PAUSED" && engine.paused_until
      ? t("priceSources.page.pausedUntil", { when: formatMoment(engine.paused_until, locale) })
      : t(`priceSources.page.statuses.${engine.status}`, { defaultValue: engine.status });
  return <span className="pill pill-warning">{text}</span>;
}

type ChangeOrder = (order: string[], said: string, focus: string[]) => void;

/** One role's stored order (D-132): disabled engines in their place, skipped; buttons to
 * move, remove and add (no drag and drop, D-196). */
function RoleOrder({
  role,
  title,
  order,
  available,
  enabled,
  known,
  busy,
  error,
  onChange,
}: {
  role: PriceRole;
  title: string;
  order: string[];
  available: string[];
  enabled: ReadonlySet<string>;
  known: ReadonlySet<string>;
  busy: boolean;
  error: unknown;
  onChange: ChangeOrder;
}) {
  const { t } = useTranslation();
  const area = `role:${role}`;
  const headingId = useId();
  const missing = available.filter((name) => !order.includes(name));
  const stale = order.some((name) => !available.includes(name));
  const label = (name: string) => engineName(name);

  return (
    <section className="ps-role" aria-labelledby={headingId}>
      <h3 id={headingId} tabIndex={-1} data-focus={`${area}|heading`}>
        {title}
      </h3>
      {order.length === 0 ? (
        <p className="muted">
          {t(available.length === 0 && role === "BOND" ? "priceSources.noBond" : "priceSources.page.noEngine")}
        </p>
      ) : (
        <OrderedList
          area={area}
          items={order}
          busy={busy}
          name={label}
          tags={(name) => [
            ...(!available.includes(name) || !known.has(name)
              ? [t("priceSources.page.notInVersion")]
              : !enabled.has(name)
                ? [t("priceSources.page.skipped")]
                : []),
          ]}
          moveSaid={(name, position) =>
            t("priceSources.page.movedRole", { name: label(name), position, count: order.length, role: title })
          }
          removeSaid={(name) => t("priceSources.page.removedRole", { name: label(name), role: title })}
          onChange={onChange}
        />
      )}
      {missing.length > 0 && (
        <div className="ps-add">
          {missing.map((name) => (
            <button
              key={name}
              type="button"
              className="button button-soft"
              aria-disabled={busy || undefined}
              data-focus={`${area}|add|${name}`}
              onClick={() => {
                if (busy) return;
                const next = [...order, name];
                const added = `${area}|${name}`;
                onChange(
                  next,
                  t("priceSources.page.addedRole", { name: label(name), position: next.length, role: title }),
                  [`${added}|up`, `${added}|remove`],
                );
              }}
            >
              {t("priceSources.page.add", { name: label(name) })}
            </button>
          ))}
        </div>
      )}
      <FormError
        error={error}
        text={
          error instanceof ApiError && error.code === "VALIDATION_FAILED"
            ? t(stale ? "priceSources.page.orderStale" : "priceSources.page.orderRefused")
            : undefined
        }
      />
    </section>
  );
}

/** An ordered list with "Su", "Giù" and "Togli" on each row: the same accessible names on
 * every screen, only the visible text changes on phones (arrows and ×). */
function OrderedList({
  area,
  items,
  busy,
  name,
  tags,
  moveSaid,
  removeSaid,
  onChange,
}: {
  area: string;
  items: string[];
  busy: boolean;
  name: (item: string) => string;
  tags: (item: string) => string[];
  moveSaid: (item: string, position: number) => string;
  removeSaid: (item: string) => string;
  onChange: ChangeOrder;
}) {
  const { t } = useTranslation();
  const move = (index: number, step: -1 | 1) => {
    if (busy) return;
    const item = items[index];
    const key = `${area}|${item}`;
    const first = step < 0 ? "up" : "down";
    const other = step < 0 ? "down" : "up";
    onChange(moved(items, index, step), moveSaid(item, index + step + 1), [
      `${key}|${first}`,
      `${key}|${other}`,
      `${key}|remove`,
    ]);
  };
  const remove = (index: number) => {
    if (busy) return;
    const item = items[index];
    const around = [items[index + 1], items[index - 1]].filter((other) => other !== undefined);
    onChange(
      items.filter((_, i) => i !== index),
      removeSaid(item),
      [...around.map((other) => `${area}|${other}|remove`), `${area}|heading`],
    );
  };
  return (
    <ol className="ps-list">
      {items.map((item, index) => {
        const shown = name(item);
        const key = `${area}|${item}`;
        return (
          <li key={item} className="ps-item">
            <span className="ps-item-name">
              <span className="ps-position" aria-hidden="true">
                {index + 1}.
              </span>
              <span className="ps-text-name">{shown}</span>
              {tags(item).map((tag) => (
                <span key={tag} className="pill pill-soft">
                  {tag}
                </span>
              ))}
            </span>
            <span className="ps-buttons">
              {index > 0 && (
                <button
                  type="button"
                  className="link-button ps-button"
                  aria-label={t("priceSources.page.moveUp", { name: shown })}
                  aria-disabled={busy || undefined}
                  data-focus={`${key}|up`}
                  onClick={() => move(index, -1)}
                >
                  <span className="ps-word">{t("priceSources.page.up")}</span>
                  <span className="ps-icon" aria-hidden="true">
                    ↑
                  </span>
                </button>
              )}
              {index < items.length - 1 && (
                <button
                  type="button"
                  className="link-button ps-button"
                  aria-label={t("priceSources.page.moveDown", { name: shown })}
                  aria-disabled={busy || undefined}
                  data-focus={`${key}|down`}
                  onClick={() => move(index, 1)}
                >
                  <span className="ps-word">{t("priceSources.page.down")}</span>
                  <span className="ps-icon" aria-hidden="true">
                    ↓
                  </span>
                </button>
              )}
              <button
                type="button"
                className="link-button danger-link ps-button"
                aria-label={t("priceSources.page.removeItem", { name: shown })}
                aria-disabled={busy || undefined}
                data-focus={`${key}|remove`}
                onClick={() => remove(index)}
              >
                <span className="ps-word">{t("priceSources.page.remove")}</span>
                <span className="ps-icon" aria-hidden="true">
                  ×
                </span>
              </button>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The preferred exchanges (D-125): checked here as the API does before sending (D-196). */
function Exchanges({
  exchanges,
  busy,
  error,
  onChange,
}: {
  exchanges: string[];
  busy: boolean;
  error: unknown;
  onChange: (next: string[], said: string, focus: string[] | null, done?: () => void) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const area = "exchanges";
  const name = (code: string) => {
    const known = exchangeName(t, code, "");
    return known && known !== code ? `${code} · ${known}` : code;
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const found = exchangeProblem(draft, exchanges);
    if (found) {
      setProblem(t(`priceSources.page.exchangeProblems.${found}`));
      return;
    }
    setProblem(null);
    const code = draft.trim();
    const next = [...exchanges, code];
    void onChange(
      next,
      t("priceSources.page.addedExchange", { name: name(code), position: next.length }),
      null,
      () => setDraft(""),
    );
  };

  const refused =
    error instanceof ApiError && error.code === "VALIDATION_FAILED"
      ? t("priceSources.page.exchangeRefused")
      : null;

  return (
    <>
      {exchanges.length === 0 ? (
        <p className="ps-empty">{t("priceSources.page.noExchanges")}</p>
      ) : (
        <OrderedList
          area={area}
          items={exchanges}
          busy={busy}
          name={name}
          tags={() => []}
          moveSaid={(item, position) =>
            t("priceSources.page.movedExchange", { name: name(item), position, count: exchanges.length })
          }
          removeSaid={(item) => t("priceSources.page.removedExchange", { name: name(item) })}
          onChange={(next, said, focus) => {
            void onChange(next, said, focus.map((key) => (key === `${area}|heading` ? `${area}|input` : key)));
          }}
        />
      )}
      {!refused && <FormError error={error} />}
      <form className="ps-exchange-form" onSubmit={submit} noValidate>
        <Field
          label={t("priceSources.page.exchangeLabel")}
          hint={t("priceSources.page.exchangeHint")}
          error={problem ?? refused ?? undefined}
          value={draft}
          announce
          data-focus={`${area}|input`}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setDraft(e.target.value);
            setProblem(null);
          }}
        />
        <button type="submit" className="button" aria-disabled={busy || undefined}>
          {t("priceSources.page.addExchange")}
        </button>
      </form>
    </>
  );
}
