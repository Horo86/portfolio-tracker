// The few Node functions the walkthrough uses to start, stop and restart its own server
// (e2e/walkthrough.spec.ts). Declared here: the frontend has no @types/node.
declare module "node:child_process" {
  export interface ChildProcess {
    pid?: number;
    exitCode: number | null;
    kill(signal?: string): boolean;
    once(event: "exit", listener: (code: number | null, signal: string | null) => void): this;
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

declare module "node:net" {
  // Only to see that the walkthrough's port is free before its server starts.
  export interface Server {
    listen(port: number, host: string, listener: () => void): this;
    close(listener: () => void): this;
    once(event: "error", listener: (error: Error) => void): this;
  }
  export function createServer(): Server;
}

declare const process: { cwd(): string; env: Record<string, string | undefined> };
