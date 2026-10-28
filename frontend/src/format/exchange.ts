import type { TFunction } from "i18next";

/** A known exchange in the user's language from its code (D-152), any other as the engine
 * names it. The table holds Yahoo's codes: the only engine that stores listings today. */
export function exchangeName(t: TFunction, code: string | null, name: string): string {
  return code ? t(`exchanges.${code}`, { defaultValue: name }) : name;
}
