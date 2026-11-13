import type { SecondFactor } from "./api/types";

// Small pure helpers of two-factor authentication (D-174).

/** What was typed without spaces (an app may show a code as "123 456"). */
export function compact(typed: string): string {
  return typed.replace(/\s/g, "");
}

/** Whether `typed` is a six-digit code of an authenticator app. */
export function isAppCode(typed: string): boolean {
  return /^\d{6}$/.test(compact(typed));
}

/** What was typed as a second factor: six digits are a code of the authenticator app,
 * anything else a recovery code (the server ignores its spaces and hyphens). */
export function secondFactor(typed: string): SecondFactor {
  return isAppCode(typed) ? { code: compact(typed) } : { recovery_code: typed.trim() };
}

/** The secret in groups of four, easier to type into an app by hand. */
export function groupSecret(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/** The path of an SVG drawing of a QR code's dark modules, `margin` modules from the edge. */
export function qrPath(rows: readonly string[], margin: number): string {
  const parts: string[] = [];
  rows.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      if (row[x] !== "1") {
        x += 1;
        continue;
      }
      let end = x;
      while (end < row.length && row[end] === "1") end += 1; // one run of dark modules
      parts.push(`M${x + margin} ${y + margin}h${end - x}v1h${x - end}z`);
      x = end;
    }
  });
  return parts.join("");
}
