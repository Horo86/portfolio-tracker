import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "./api/client";
import type {
  ImportAssetChoice,
  ImportConfirmIn,
  ImportConfirmOut,
  ImportFormat,
  ImportIn,
  ImportLine,
  ImportPreview,
} from "./api/types";
import { afterChange, ASSETS_KEY } from "./transactions";

// The import screens' logic (D-100, D-113, D-142). The file is read in the browser, kept in
// memory only (never stored) and sent again with every preview and with the confirmation;
// nothing is saved before the user confirms (hard rule 6).

export const MAX_FILE_BYTES = 10 * 1024 * 1024; // D-101, checked before the file is read
export const TICK_DELAY_MS = 400; // a new preview once the ticks stop changing
export const BUSY_RETRIES = 3; // IMPORT_BUSY: automatic retries before "try again"
const DEFAULT_RETRY_AFTER_SECONDS = 5;

/** A file read for upload: its content as base64 (D-101), held only by the page. */
export type LoadedFile = { id: number; name: string; size: number; base64: string };

let files = 0;

export function fitsUpload(file: { size: number }): boolean {
  return file.size <= MAX_FILE_BYTES;
}

/** Reads the chosen file as base64. Check `fitsUpload` first: a larger file is not read. */
export function readFile(file: File): Promise<LoadedFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result); // data:<type>;base64,<content>
      files += 1;
      // An empty file: Chromium gives "data:<type>;base64," (checked end to end); a reader
      // that gives "data:" alone must not send those five characters as the content.
      const comma = url.indexOf(",");
      const base64 = comma === -1 ? "" : url.slice(comma + 1);
      resolve({ id: files, name: file.name, size: file.size, base64 });
    };
    reader.onerror = () => reject(reader.error ?? new Error("the file could not be read"));
    reader.readAsDataURL(file);
  });
}

/** The user's choices, sent with every preview and with the confirmation. */
export type ImportChoices = {
  format: ImportFormat | null; // null: detected by the server
  moveCash: boolean;
  accountId: string | null; // Directa: the target account (null: the one remembering the code)
  accounts: Readonly<Record<string, string | null>>; // app CSV: file name -> account or new
  assets: Readonly<Record<string, ImportAssetChoice>>;
};

const START: ImportChoices = {
  format: null,
  moveCash: true,
  accountId: null,
  accounts: {},
  assets: {},
};

/** `next`, or `current` itself when nothing changed: a choice set to its current value must
 * not ask the same preview again (the preview follows the choices object). */
function edited(current: ImportChoices, next: ImportChoices): ImportChoices {
  return JSON.stringify(next) === JSON.stringify(current) ? current : next;
}

export type Ticks = ReadonlyMap<string, boolean>;
const NO_TICKS: Ticks = new Map();

/**
 * The line keys to send: null, i.e. the server's default selection, until the user ticks
 * something; then the selectable lines of the latest preview, each as the user ticked it or
 * as the default (to import: yes; to review, skipped by choice: no).
 */
export function selectionOf(preview: ImportPreview | null, ticks: Ticks): string[] | null {
  if (preview === null || ticks.size === 0) return null;
  return preview.lines
    .filter((l) => l.selectable && (ticks.get(l.key) ?? l.category === "TO_IMPORT"))
    .map((l) => l.key);
}

/** Whether a line shows as ticked: the user's tick, or what the preview says. */
export function isTicked(line: ImportLine, ticks: Ticks): boolean {
  if (ticks.size === 0) return line.selected;
  return ticks.get(line.key) ?? line.category === "TO_IMPORT";
}

export function requestOf(
  file: LoadedFile,
  choices: ImportChoices,
  selected: string[] | null,
): ImportIn {
  return {
    content_base64: file.base64,
    file_name: file.name,
    format: choices.format,
    move_cash: choices.moveCash,
    account_id: choices.accountId,
    accounts: { ...choices.accounts },
    assets: { ...choices.assets },
    selected,
  };
}

/** While the server is busy with other imports: when the next attempt goes, and which. */
export type Wait = { until: number; attempt: number };

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    // Aborted while the last attempt was on its way: an abort event will not come again.
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

/** `call`, tried again after `Retry-After` while the server answers IMPORT_BUSY, at most
 * BUSY_RETRIES times; then the error goes to the caller ("try again"). */
export async function withBusyRetry<T>(
  call: () => Promise<T>,
  onWait: (wait: Wait | null) => void,
  signal?: AbortSignal,
): Promise<T> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await call();
    } catch (error) {
      const busy = error instanceof ApiError && error.code === "IMPORT_BUSY";
      if (!busy || attempt > BUSY_RETRIES) throw error;
      const ms = (error.retryAfter ?? DEFAULT_RETRY_AFTER_SECONDS) * 1000;
      onWait({ until: Date.now() + ms, attempt });
      try {
        await sleep(ms, signal);
      } finally {
        onWait(null);
      }
    }
  }
}

/** After an import: lists, account figures and securities are out of date. */
export function afterImport(client: QueryClient): Promise<unknown> {
  return Promise.all([afterChange(client), client.invalidateQueries({ queryKey: ASSETS_KEY })]);
}

type Answer = { key: string; preview: ImportPreview };
type Failure = { key: string; error: unknown };
export type ImportNotice = "selectionReset" | "stale";

// After these answers to a confirmation the preview is asked again: the data, a choice or
// the dry run changed; the new preview shows what to do.
const PREVIEW_AGAIN = new Set(["IMPORT_PREVIEW_STALE", "QUANTITY_CONFLICT", "IMPORT_NOT_READY"]);

/**
 * One import, from the file to the summary. Every change of a choice or a tick asks for a
 * new preview (D-113); a request whose input changed meanwhile is aborted, and an answer is
 * shown only for the input it was asked for. Ticks are kept across previews after an asset
 * choice or a tick, and go back to the default when the account or the option changes, or
 * when the server says the selection no longer fits (D-142).
 */
export function useImport() {
  const client = useQueryClient();
  const [file, setFile] = useState<LoadedFile | null>(null);
  const [choices, setChoices] = useState<ImportChoices>(START);
  const [ticks, setTicks] = useState<Ticks>(NO_TICKS);
  const [attempt, setAttempt] = useState(0); // "try again", or the data changed
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [wait, setWait] = useState<Wait | null>(null);
  const [notice, setNotice] = useState<ImportNotice | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<unknown>(null);
  const [done, setDone] = useState<ImportConfirmOut | null>(null);
  const ticked = useRef(false); // the last change was a tick: wait before asking
  const sending = useRef(false); // a confirmation is on its way: never a second one
  const stopWait = useRef<AbortController | null>(null); // the confirmation's busy wait

  const preview = answer?.preview ?? null;
  const selected = useMemo(() => selectionOf(preview, ticks), [preview, ticks]);
  // Compared as text, so an equal selection from a new preview asks nothing new.
  const selectedText = selected === null ? null : selected.join("\n");
  const key =
    file === null ? null : JSON.stringify([file.id, choices, selectedText, attempt]);

  useEffect(() => {
    if (file === null || key === null) return;
    const keys = selectedText === null ? null : selectedText.split("\n").filter(Boolean);
    const body = requestOf(file, choices, keys);
    const controller = new AbortController();
    const delay = ticked.current ? TICK_DELAY_MS : 0;
    ticked.current = false;
    const timer = window.setTimeout(() => {
      withBusyRetry(
        () => api<ImportPreview>("POST", "/imports/preview", body, controller.signal),
        setWait,
        controller.signal,
      ).then(
        (result) => {
          if (controller.signal.aborted) return;
          setAnswer({ key, preview: result });
          setFailure(null);
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && error.code === "IMPORT_SELECTION_INVALID") {
            setTicks(NO_TICKS);
            setNotice("selectionReset");
            return;
          }
          setFailure({ key, error });
        },
      );
    }, delay);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [file, choices, selectedText, attempt, key]);

  type First = Pick<ImportChoices, "format" | "moveCash">;
  const start = useCallback((next: LoadedFile, first: First) => {
    setFile(next);
    setChoices({ ...START, ...first });
    setTicks(NO_TICKS);
    setAnswer(null);
    setFailure(null);
    setNotice(null);
    setDone(null);
    setConfirmError(null);
  }, []);

  // Leaving the page stops a confirmation waiting for a busy server: nothing is sent later.
  useEffect(() => () => stopWait.current?.abort(), []);

  /** Back to the first step; the file is let go. A confirmation waiting for a busy server
   * stops there (one already sent is not called back: the page waits for its answer). */
  const reset = useCallback(() => {
    stopWait.current?.abort();
    setFile(null);
    setChoices(START);
    setTicks(NO_TICKS);
    setAnswer(null);
    setFailure(null);
    setNotice(null);
    setDone(null);
    setConfirmError(null);
  }, []);

  // While a confirmation is on its way (also while it waits for a busy server) the preview it
  // confirms stays as shown: a choice or a tick then is ignored (the page is inert too).
  const change = useCallback((next: Partial<ImportChoices>, keepTicks: boolean) => {
    if (sending.current) return;
    setChoices((current) => edited(current, { ...current, ...next }));
    if (!keepTicks) setTicks(NO_TICKS);
    setNotice(null);
    setConfirmError(null);
  }, []);

  const setMoveCash = useCallback((moveCash: boolean) => change({ moveCash }, false), [change]);
  const setAccount = useCallback((accountId: string) => change({ accountId }, false), [change]);
  const mapAccount = useCallback((name: string, accountId: string | null) => {
    if (sending.current) return;
    setChoices((current) =>
      edited(current, { ...current, accounts: { ...current.accounts, [name]: accountId } }),
    );
    setTicks(NO_TICKS);
    setNotice(null);
    setConfirmError(null);
  }, []);
  const chooseAsset = useCallback((assetKey: string, choice: ImportAssetChoice) => {
    if (sending.current) return;
    setChoices((current) =>
      edited(current, {
        ...current,
        assets: { ...current.assets, [assetKey]: { ...current.assets[assetKey], ...choice } },
      }),
    );
    setNotice(null);
    setConfirmError(null);
  }, []);

  /** Ticks or unticks lines; the preview is asked again once the ticks stop changing. */
  const tick = useCallback((keys: readonly string[], on: boolean) => {
    if (sending.current) return;
    ticked.current = true;
    setTicks((current) => {
      const next = new Map(current);
      for (const k of keys) next.set(k, on);
      return next;
    });
    setNotice(null);
    setConfirmError(null);
  }, []);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const current = answer !== null && answer.key === key;
  const pending = key !== null && !current && failure?.key !== key;

  const confirm = useCallback(async () => {
    if (file === null || answer === null || answer.key !== key || sending.current) return;
    const shown = answer.preview;
    const body: ImportConfirmIn = {
      ...requestOf(file, choices, null),
      fingerprint: shown.fingerprint,
      selected: shown.lines.filter((l) => l.selected).map((l) => l.key),
    };
    const controller = new AbortController(); // stops the waits only, never a request sent
    stopWait.current = controller;
    sending.current = true;
    setConfirming(true);
    setConfirmError(null);
    try {
      const out = await withBusyRetry(
        () => api<ImportConfirmOut>("POST", "/imports/confirm", body),
        setWait,
        controller.signal,
      );
      // The summary names the accounts the import created: lists first, then the summary.
      await afterImport(client);
      setDone(out);
      setFile(null); // the file is no longer needed: let it go
    } catch (error) {
      if (controller.signal.aborted) return; // cancelled, or the page was left
      if (error instanceof ApiError && PREVIEW_AGAIN.has(error.code)) {
        if (error.code === "IMPORT_PREVIEW_STALE") setNotice("stale");
        setAttempt((n) => n + 1);
      }
      setConfirmError(error);
    } finally {
      stopWait.current = null;
      sending.current = false;
      setConfirming(false);
    }
  }, [file, answer, key, choices, client]);

  return {
    file,
    choices,
    ticks,
    preview,
    current,
    pending,
    error: failure?.key === key ? failure.error : null,
    wait,
    notice,
    dismissNotice: () => setNotice(null),
    confirming,
    confirmError,
    done,
    start,
    reset,
    setMoveCash,
    setAccount,
    mapAccount,
    chooseAsset,
    tick,
    retry,
    confirm,
  };
}
