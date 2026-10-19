import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchHealth, type Health } from "./health";
import { LANGUAGE_KEY, LANGUAGES, type Language } from "./i18n";
import { writePreference } from "./storage";
import type { ThemeName } from "./api/types";
import { applyTheme, storedTheme, THEMES } from "./theme";

type HealthState = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error" };

export default function App() {
  const { t, i18n } = useTranslation();
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [theme, setTheme] = useState<ThemeName>(storedTheme);

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((result) =>
        setHealth(result.status === "ok" ? { kind: "ok", health: result } : { kind: "error" }),
      )
      .catch(() => {
        if (!controller.signal.aborted) setHealth({ kind: "error" });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => applyTheme(theme), [theme]);

  useEffect(() => {
    document.documentElement.lang = i18n.language;
    document.title = t("app.title");
  }, [i18n.language, t]);

  const changeLanguage = (language: Language) => {
    writePreference(LANGUAGE_KEY, language);
    void i18n.changeLanguage(language);
  };

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>{t("app.title")}</h1>
          <p className="muted">{t("app.tagline")}</p>
        </div>
        <div className="controls">
          <label>
            {t("settings.language")}
            <select
              value={i18n.language}
              onChange={(event) => changeLanguage(event.target.value as Language)}
            >
              {LANGUAGES.map((language) => (
                <option key={language} value={language}>
                  {t(`languages.${language}`)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("settings.theme")}
            <select value={theme} onChange={(event) => setTheme(event.target.value as ThemeName)}>
              {THEMES.map((option) => (
                <option key={option} value={option}>
                  {t(`settings.themes.${option}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <main>
        <section className="card" aria-live="polite">
          <h2>{t("health.heading")}</h2>
          {health.kind === "loading" && <p>{t("health.loading")}</p>}
          {health.kind === "ok" && (
            <p className="ok">{t("health.ok", { version: health.health.version })}</p>
          )}
          {health.kind === "error" && <p className="error">{t("health.error")}</p>}
        </section>
      </main>
    </div>
  );
}
