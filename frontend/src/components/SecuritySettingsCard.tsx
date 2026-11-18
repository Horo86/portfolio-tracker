import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type {
  SecuritySettingName,
  SecuritySettings,
  SecuritySettingsSaved,
} from "../api/types";
import { formatMomentDay } from "../format/date";
import { useScreenTexts } from "../i18n";
import {
  type Change,
  changesOf,
  type Drafts,
  draftsOf,
  isChanged,
  type Problems,
  problemsOf,
  SECURITY_FIELDS,
  valuesOf,
} from "../securitySettings";
import { SECURITY_SETTINGS_TEXTS } from "../securitySettingsTexts";
import { Field } from "./Field";
import { Notice, useNotice } from "./Notice";
import { ReauthDialog } from "./ReauthDialog";
import { LoadError, Loading } from "./Status";

export const SECURITY_SETTINGS_KEY = ["admin", "security-settings"] as const;

const GROUPS: readonly { legend: string; fields: readonly SecuritySettingName[] }[] = [
  { legend: "sessions", fields: ["session_idle_days", "session_absolute_days"] },
  {
    legend: "logins",
    fields: [
      "login_account_failures",
      "login_address_failures",
      "block_first_minutes",
      "block_max_minutes",
    ],
  },
];

/**
 * The "Security" card of the administration area (D-182; no mockup, built like the Settings
 * rows): the session durations and the lockout thresholds, saved after the administrator's
 * password and, with two-factor authentication, a code (D-181). The password and the code
 * stay in the dialog (`ReauthDialog`), never in a cache.
 */
export function SecuritySettingsCard({
  twoFactor,
  withoutTwoFactor,
}: {
  twoFactor: boolean;
  withoutTwoFactor?: number; // active users without two-factor sign-in, once the list is read
}) {
  useScreenTexts(SECURITY_SETTINGS_TEXTS);
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const headingId = useId();
  const query = useQuery({
    queryKey: SECURITY_SETTINGS_KEY,
    queryFn: ({ signal }) =>
      api<SecuritySettings>("GET", "/admin/security-settings", undefined, signal),
  });
  const data = query.data;

  return (
    <section className="card security-settings" aria-labelledby={headingId}>
      <h2 id={headingId} className="card-label">
        {t("securitySettings.heading")}
      </h2>
      <p className="muted">{t("securitySettings.intro")}</p>
      {query.isPending && <Loading />}
      {query.isError && (
        <LoadError error={query.error} onRetry={() => void query.refetch()} level={3} />
      )}
      {data && (
        <>
          <p className="muted small">
            {data.source === "database" && data.updated_at
              ? t("securitySettings.sourceDatabase", {
                  date: formatMomentDay(data.updated_at, i18n.language),
                })
              : t("securitySettings.sourceEnvironment")}
          </p>
          <SecurityForm
            data={data}
            twoFactor={twoFactor}
            withoutTwoFactor={withoutTwoFactor}
            onSaved={(out) => client.setQueryData<SecuritySettings>(SECURITY_SETTINGS_KEY, out)}
          />
        </>
      )}
    </section>
  );
}

function SecurityForm({
  data,
  twoFactor,
  withoutTwoFactor,
  onSaved,
}: {
  data: SecuritySettings;
  twoFactor: boolean;
  withoutTwoFactor?: number;
  onSaved: (out: SecuritySettingsSaved) => void;
}) {
  const { t } = useTranslation();
  const everyoneId = useId();
  const current = data.settings;
  const [drafts, setDrafts] = useState<Drafts>(() => draftsOf(current));
  const [problems, setProblems] = useState<Problems>({});
  const [confirming, setConfirming] = useState<Change[] | null>(null);
  const { notice, announce } = useNotice();
  const refs = useRef<Partial<Record<SecuritySettingName, HTMLInputElement | null>>>({});
  const saveRef = useRef<HTMLButtonElement>(null);
  const focusSave = useRef(false);
  const changed = isChanged(drafts, current);

  // After a save the dialog closes and gives the focus back to its opener; "Save" is said
  // explicitly too, since its state changes in the same render.
  useEffect(() => {
    if (confirming !== null || !focusSave.current) return;
    focusSave.current = false;
    saveRef.current?.focus();
  }, [confirming]);

  const label = (name: SecuritySettingName) => t(`securitySettings.fields.${name}.label`);
  const said = (change: Change) =>
    change.name === "require_2fa_all_users"
      ? t("securitySettings.change", {
          label: t("securitySettings.everyone"),
          from: t(change.from ? "securitySettings.yes" : "securitySettings.no"),
          to: t(change.to ? "securitySettings.yes" : "securitySettings.no"),
        })
      : t("securitySettings.change", { label: label(change.name), from: change.from, to: change.to });
  const set = (name: SecuritySettingName) => (e: { target: { value: string } }) =>
    setDrafts((now) => ({ ...now, [name]: e.target.value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!changed) return;
    const found = problemsOf(drafts, data.bounds);
    setProblems(found);
    const first = SECURITY_FIELDS.find((name) => found[name]);
    if (first) {
      refs.current[first]?.focus();
      return;
    }
    setConfirming(changesOf(drafts, current));
  };

  return (
    <>
      <Notice notice={notice} />
      <form onSubmit={submit} noValidate>
        {GROUPS.map((group) => (
          <fieldset key={group.legend} className="settings-group">
            <legend>{t(`securitySettings.groups.${group.legend}`)}</legend>
            {group.fields.map((name) => {
              const problem = problems[name];
              return (
                <Field
                  key={name}
                  label={label(name)}
                  hint={t(`securitySettings.fields.${name}.hint`)}
                  inputMode="numeric"
                  autoComplete="off"
                  className="input-short"
                  value={drafts[name]}
                  onChange={set(name)}
                  error={problem && t(problem.key, problem.values)}
                  inputRef={(element) => {
                    refs.current[name] = element;
                  }}
                />
              );
            })}
          </fieldset>
        ))}
        {/* D-183: only adds an obligation; PT_REQUIRE_2FA still decides for administrators. */}
        <fieldset className="settings-group">
          <legend>{t("securitySettings.groups.twoFactor")}</legend>
          <div className="check">
            <input
              id={everyoneId}
              type="checkbox"
              checked={drafts.require_2fa_all_users}
              onChange={(e) => {
                const on = e.target.checked;
                setDrafts((now) => ({ ...now, require_2fa_all_users: on }));
              }}
              aria-describedby={withoutTwoFactor === undefined ? undefined : `${everyoneId}-hint`}
            />
            <label htmlFor={everyoneId}>{t("securitySettings.everyone")}</label>
            {withoutTwoFactor !== undefined && (
              <span id={`${everyoneId}-hint`} className="field-hint">
                {t("securitySettings.everyoneHint", { count: withoutTwoFactor })}
              </span>
            )}
          </div>
        </fieldset>
        <div className="actions actions-start">
          <button
            ref={saveRef}
            type="submit"
            className="button button-primary"
            // Not `disabled`: the focus comes back here after a save, when nothing differs.
            aria-disabled={!changed}
          >
            {t("securitySettings.save")}
          </button>
        </div>
      </form>
      {confirming && (
        <ReauthDialog
          heading={t("securitySettings.dialogHeading")}
          submit={t("securitySettings.save")}
          method="PUT"
          path="/admin/security-settings"
          body={valuesOf(drafts)}
          withCode={twoFactor}
          danger={false}
          passwordLabel={t("admin.yourPassword")}
          passwordHint={t("admin.yourPasswordHint")}
          codeLabel={t("admin.yourCode")}
          codeHint={t("admin.yourCodeHint")}
          onClose={() => setConfirming(null)}
          onDone={(out: SecuritySettingsSaved) => {
            onSaved(out);
            setDrafts(draftsOf(out.settings));
            setProblems({});
            focusSave.current = true;
            setConfirming(null);
            const said = [t("securitySettings.saved")];
            if (out.sessions_shortened > 0)
              said.push(t("securitySettings.shortened", { count: out.sessions_shortened }));
            announce(said.join(" "));
          }}
        >
          <ul className="change-list">
            {confirming.map((change) => (
              <li key={change.name}>{said(change)}</li>
            ))}
          </ul>
        </ReauthDialog>
      )}
    </>
  );
}
