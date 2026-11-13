// The one Node function the TOTP helper of e2e/helpers.ts uses (RFC 6238 with HMAC-SHA1).
// Declared here: the frontend has no @types/node.
declare module "node:crypto" {
  export interface Hmac {
    update(data: Uint8Array): Hmac;
    digest(): Uint8Array;
  }
  export function createHmac(algorithm: "sha1", key: Uint8Array): Hmac;
}
