import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Me, RecoveryCodesOut, TwoFactorChanged } from "../api/types";
import { ME_KEY } from "../session";
import { Dialog } from "./Dialog";
import { focusPageTitle } from "./PageTitle";
import { ReauthDialog } from "./ReauthDialog";
import { RecoveryCodes, TwoFactorEnrol } from "./TwoFactorEnrol";

type Open = "on" | "off" | "codes" | { codes: string[]; user: Me } | null;

/** The heading of a new step inside a dialog: it takes the focus, since what had it (the
 * step's button) is gone, and a screen reader reads where the dialog is now. */
function StepHeading({ text }: { text: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => ref.current?.focus(), []);
  return (
    <h3 ref={ref} tabIndex={-1}>
      {text}
    </h3>
  );
}

/**
 * Mockup 14's 2FA row (D-174 (7)): turn it on (password, QR code, a code, the recovery codes
 * once), turn it off and make new recovery codes (each with the password and a code). Each
 * change ends the other sessions; the answer's user goes into the `me` query, so an
 * administrator under the obligation who turns it off is taken back to the enrolment.
 */
export function TwoFactorSettings({
  me,
  onChanged,
}: {
  me: Me;
  onChanged: (notice: string) => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [open, setOpen] = useState<Open>(null);
  const opener = useRef<HTMLElement | null>(null);
  const show = (from: HTMLElement, next: Open) => {
    opener.current = from;
    setOpen(next);
  };
  // When the flow ends the focus goes back to its button; once that is replaced by the change
  // (Turn on ↔ Turn off), to the page's heading. The page's live region says what happened.
  useEffect(() => {
    if (open !== null || opener.current === null) return;
    const from = opener.current;
    opener.current = null;
    if (from.isConnected) from.focus();
    else focusPageTitle();
  }, [open]);
  const changed = (user: Me, notice: string) => {
    client.setQueryData(ME_KEY, user);
    void client.invalidateQueries({ queryKey: ["sessions"] }); // the other ones ended
    setOpen(null);
    onChanged(notice);
  };
  const codeLabels = {
    passwordLabel: t("twoFactor.settings.password"),
    codeLabel: t("twoFactor.settings.code"),
    codeHint: t("twoFactor.settings.codeHint"),
    withCode: true,
    onClose: () => setOpen(null),
  };

  return (
    <>
      <div className="row">
        <div className="row-text">
          <span className="row-label">{t("twoFactor.settings.row")}</span>
          <span className="muted small">
            {me.two_factor_enabled
              ? t("twoFactor.settings.on", { count: me.recovery_codes_left ?? 0 })
              : t("twoFactor.settings.off")}
          </span>
        </div>
        <div className="row-control">
          {me.two_factor_enabled ? (
            <div className="row-buttons">
              <button
                type="button"
                className="button"
                onClick={(e) => show(e.currentTarget, "codes")}
              >
                {t("twoFactor.settings.newCodes")}
              </button>
              <button type="button" className="button" onClick={(e) => show(e.currentTarget, "off")}>
                {t("twoFactor.settings.turnOff")}
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="button button-primary"
              onClick={(e) => show(e.currentTarget, "on")}
            >
              {t("twoFactor.settings.turnOn")}
            </button>
          )}
        </div>
      </div>

      {open === "on" && (
        <Dialog title={t("twoFactor.setup.heading")} onClose={() => setOpen(null)}>
          <TwoFactorEnrol
            heading={(title, step) =>
              step === "password" ? null : (
                <StepHeading
                  key={step}
                  text={step === "scan" ? t("twoFactor.setup.scanHeading") : title}
                />
              )
            }
            onCancel={() => setOpen(null)}
            onFinished={(user) => changed(user, t("twoFactor.settings.onNotice"))}
          />
        </Dialog>
      )}
      {open === "off" && (
        <ReauthDialog
          {...codeLabels}
          heading={t("twoFactor.settings.offHeading")}
          text={t("twoFactor.settings.offText")}
          submit={t("twoFactor.settings.turnOff")}
          path="/me/two-factor/disable"
          onDone={(out: TwoFactorChanged) => changed(out.user, t("twoFactor.settings.offNotice"))}
        />
      )}
      {open === "codes" && (
        <ReauthDialog
          {...codeLabels}
          danger={false}
          heading={t("twoFactor.settings.codesHeading")}
          text={t("twoFactor.settings.codesText")}
          submit={t("twoFactor.settings.create")}
          path="/me/two-factor/recovery-codes"
          // Shown once, in this component's state only, until "I saved the codes".
          onDone={(out: RecoveryCodesOut) => setOpen({ codes: out.recovery_codes, user: out.user })}
        />
      )}
      {open !== null && typeof open === "object" && (
        // Escape does not close it: the codes are shown once, so "I saved the codes" does.
        <Dialog title={t("twoFactor.codes.heading")} onClose={() => {}}>
          <RecoveryCodes
            codes={open.codes}
            onSaved={() => changed(open.user, t("twoFactor.settings.codesNotice"))}
          />
        </Dialog>
      )}
    </>
  );
}
