// The few Node functions the walkthrough uses to start, stop and restart its own server
// (e2e/walkthrough.spec.ts). Declared here: the frontend has no @types/node.
declare module "node:child_process" {
  export interface ChildProcess {
    pid?: number;
    exitCode: number | null;
    kill(signal?: string): boolean;
    once(event: "exit", listener: (code: number | null) => void): this;
  }
  export function spawn(
    command: string,
    args: string[],
    options: { cwd?: string; env?: Record<string, string | undefined>; stdio?: "inherit" | "ignore" },
  ): ChildProcess;
}

declare module "node:fs" {
  export function rmSync(path: string, options: { recursive: boolean; force: boolean }): void;
  export function mkdirSync(path: string, options: { recursive: boolean }): void;
}

declare const process: { cwd(): string; env: Record<string, string | undefined> };
