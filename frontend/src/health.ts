export type Health = { status: "ok" | "error"; version: string; database: "ok" | "error" };

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/health", { signal });
  if (!response.ok) throw new Error(`health check failed: ${response.status}`);
  return (await response.json()) as Health;
}
