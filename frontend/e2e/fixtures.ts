import { type BrowserContext, test as base, expect } from "@playwright/test";

export * from "@playwright/test";

/** Collects every Content Security Policy violation of the context's pages (D-163): the
 * browser's `securitypolicyviolation` event, and its console message as a second witness. */
export async function watchCsp(context: BrowserContext): Promise<string[]> {
  const violations: string[] = [];
  await context.exposeBinding("__reportCspViolation", (_source, text: string) => {
    violations.push(text);
  });
  await context.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      const report = (window as unknown as { __reportCspViolation: (t: string) => void })
        .__reportCspViolation;
      report(
        `${event.effectiveDirective} blocked ${event.blockedURI || "inline"} ` +
          `at ${event.sourceFile}:${event.lineNumber}`,
      );
    });
  });
  context.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) violations.push(message.text());
  });
  return violations;
}

/** Every spec's browser context fails its test on any CSP violation (D-163). */
export const test = base.extend({
  context: async ({ context }, provide) => {
    const violations = await watchCsp(context);
    await provide(context);
    expect(violations, "Content Security Policy violations").toEqual([]);
  },
});
