import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ACCOUNTS_KEY, useAccountInView } from "../accounts";
import { api } from "../api/client";
import type { Language, Me, SessionInfo, ThemeName } from "../api/types";
import { CurrencySelect } from "../components/CurrencySelect";
import { Dialog } from "../components/Dialog";
import { SelectField } from "../components/Field";
import { FormError } from "../components/FormError";
import { PageTitle } from "../components/PageTitle";
import { PasswordChangeForm } from "../components/PasswordChangeForm";
import { Notice, useNotice } from "../components/Notice";
import { LoadError, Loading } from "../components/Status";
import { formatMoment, formatMomentDay } from "../format/date";
import { deviceOf } from "../format/device";
import { LANGUAGES } from "../i18n";
import { setLanguage, syncPreferences } from "../preferences";
import { useSecretMutation } from "../secretMutation";
import { ME_KEY, useMe } from "../session";
import { applyTheme, THEMES } from "../theme";
import { PASSWORD_MIN } from "../validation";

const SESSIONS_KEY = ["sessions"] as const;
const EXPORT_URL = "/api/v1/transactions/export.csv";

type Preferences = Partial<Pick<Me, "language" | "theme" | "reporting_currency">>;

/** Mockup 14. The 2FA row is M6; the backup note belongs to the administration (M6). */
export default function Settings() {
  const { t } = useTranslation();
  const me = useMe().data;
  const { notice, announce } = useNotice();
  if (!me) return <Loading />;
  return (
    <>
      <PageTitle title={t("settings.heading")} />
      <p className="muted page-intro">{t("settings.intro")}</p>
      <Notice notice={notice} />
      <div className="stack">
        <PreferencesSections me={me} onNotice={announce} />
        <SecuritySection me={me} onNotice={announce} />
        <Section title={t("settings.data")}>
          <Row label={t("settings.export")} hint={t("settings.exportHint")}>
            <a className="button" href={EXPORT_URL} download>
              {t("settings.download")}
            </a>
          </Row>
        </Section>
        <AboutSection />
      </div>
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card settings-section">
      <h2>{title}</h2>
      <div className="rows">{children}</div>
    </section>
  );
}

/** A setting: its name and hint on the left, the control on the right. */
function Row({
  label,
  hint,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="row">
      <div className="row-text">
        <span className="row-label">{label}</span>
        {hint && <span className="muted small">{hint}</span>}
      </div>
      {children && <div className="row-control">{children}</div>}
    </div>
  );
}

/** Saves preferences at once (PATCH /me); each section has its own, so an error shows next
 * to the control that caused it. */
function useSavePreferences(me: Me, onNotice: (text: string) => void) {
  const { t } = useTranslation();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (changes: Preferences) => api<Me>("PATCH", "/me", changes),
    onSuccess: (updated, changes) => {
      client.setQueryData(ME_KEY, updated);
      // The value of each account in the reporting currency changes with it.
      if (changes.reporting_currency) void client.invalidateQueries({ queryKey: ACCOUNTS_KEY });
      onNotice(t("settings.saved"));
    },
    // Theme and language are shown at once, so undone if the server refuses them.
    onError: () => syncPreferences(me),
  });
}

/** Theme, language and reporting currency. */
function PreferencesSections({ me, onNotice }: { me: Me; onNotice: (text: string) => void }) {
  const { t, i18n } = useTranslation();
  const save = useSavePreferences(me, onNotice);
  const saveCurrency = useSavePreferences(me, onNotice);

  const chooseTheme = (theme: ThemeName) => {
    applyTheme(theme);
    save.mutate({ theme });
  };
  const chooseLanguage = (language: Language) => {
    setLanguage(language);
    save.mutate({ language });
  };

  return (
    <>
      <Section title={t("settings.appearance")}>
        <FormError error={save.error} />
        <Row label={t("settings.colours")} hint={t("settings.coloursHint")}>
          <fieldset className="theme-choice">
            <legend className="visually-hidden">{t("settings.colours")}</legend>
            {THEMES.map((theme) => (
              <label key={theme} className="theme-option">
                <input
                  type="radio"
                  name="theme"
                  value={theme}
                  checked={me.theme === theme}
                  onChange={() => chooseTheme(theme)}
                  className="visually-hidden"
                />
                <span className="swatch" data-theme={theme} aria-hidden="true" />
                {t(`settings.themes.${theme}`)}
              </label>
            ))}
          </fieldset>
        </Row>
        <Row label={t("settings.language")} hint={t("settings.languageHint")}>
          <SelectField
            label={t("settings.language")}
            hideLabel
            value={i18n.language}
            onChange={(e) => chooseLanguage(e.target.value as Language)}
          >
            {LANGUAGES.map((language) => (
              <option key={language} value={language}>
                {t(`languages.${language}`)}
              </option>
            ))}
          </SelectField>
        </Row>
      </Section>
      <Section title={t("settings.currencySection")}>
        <FormError error={saveCurrency.error} />
        <Row label={t("settings.reporting")} hint={t("settings.reportingHint")}>
          <CurrencySelect
            label={t("settings.reporting")}
            hideLabel
            value={me.reporting_currency}
            onChange={(code) => saveCurrency.mutate({ reporting_currency: code })}
          />
        </Row>
      </Section>
    </>
  );
}

function SecuritySection({ me, onNotice }: { me: Me; onNotice: (text: string) => void }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [changing, setChanging] = useState(false);
  const sessions = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: ({ signal }) => api<SessionInfo[]>("GET", "/me/sessions", undefined, signal),
  });
  const change = useSecretMutation(
    ({ current, next }: { current: string; next: string }) =>
      api<Me>("POST", "/me/password", { current_password: current, new_password: next }),
    {
      onSuccess: (updated) => {
        client.setQueryData(ME_KEY, updated);
        void client.invalidateQueries({ queryKey: SESSIONS_KEY });
        setChanging(false);
        onNotice(t("settings.passwordDone"));
      },
    },
  );
  const logoutOthers = useMutation({
    mutationFn: () => api<{ revoked: number }>("POST", "/auth/logout-others"),
    onSuccess: ({ revoked }) => {
      void client.invalidateQueries({ queryKey: SESSIONS_KEY });
      onNotice(t("settings.loggedOut", { count: revoked }));
    },
  });
  const others = sessions.data?.filter((s) => !s.current).length ?? 0;
  const locale = i18n.language;

  return (
    <Section title={t("settings.security")}>
      <Row
        label={t("settings.password")}
        hint={t("settings.passwordChanged", {
          date: formatMomentDay(me.password_changed_at, locale),
        })}
      >
        <button
          type="button"
          className="button"
          onClick={() => {
            change.reset();
            setChanging(true);
          }}
        >
          {t("password.submit")}
        </button>
      </Row>
      <Row label={t("settings.sessions")}>
        <button
          type="button"
          className="button"
          onClick={() => logoutOthers.mutate()}
          disabled={others === 0 || logoutOthers.isPending}
        >
          {t("settings.logoutOthers")}
        </button>
      </Row>
      <FormError error={logoutOthers.error} />
      {sessions.isPending && <Loading />}
      {sessions.isError && (
        <LoadError error={sessions.error} onRetry={() => void sessions.refetch()} />
      )}
      {sessions.data && (
        <ul className="sessions" aria-label={t("settings.sessions")}>
          {sessions.data.map((session) => (
            <SessionItem key={session.id} session={session} />
          ))}
        </ul>
      )}
      {changing && (
        <Dialog title={t("password.submit")} onClose={() => setChanging(false)}>
          <p className="muted">{t("settings.passwordIntro", { min: PASSWORD_MIN })}</p>
          <PasswordChangeForm
            onSubmit={(current, next) => change.submit({ current, next })}
            pending={change.isPending}
            error={change.error}
            actions={
              <button type="button" className="button" onClick={() => setChanging(false)}>
                {t("common.cancel")}
              </button>
            }
          />
        </Dialog>
      )}
    </Section>
  );
}

function SessionItem({ session }: { session: SessionInfo }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const device = session.user_agent ? deviceOf(session.user_agent) : null;
  const name = device
    ? t("settings.device", device)
    : (session.user_agent ?? t("settings.unknownDevice"));
  return (
    <li>
      <span className="session-name">
        {name}
        {session.current && <span className="pill pill-soft">{t("settings.thisDevice")}</span>}
      </span>
      <span className="muted small">
        {t("settings.sessionDates", {
          created: formatMoment(session.created_at, locale),
          used: formatMoment(session.last_used_at, locale),
        })}
      </span>
    </li>
  );
}

function AboutSection() {
  const { t } = useTranslation();
  const { search } = useAccountInView();
  return (
    <Section title={t("settings.aboutSection")}>
      <Row label={t("settings.aboutApp")} hint={t("settings.aboutHint")}>
        <Link to={{ pathname: "/about", search }} className="button">
          {t("settings.open")}
        </Link>
      </Row>
    </Section>
  );
}
