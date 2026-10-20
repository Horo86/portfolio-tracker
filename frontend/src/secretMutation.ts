import { useMutation } from "@tanstack/react-query";
import { useRef } from "react";

type Handlers<T> = { onSuccess?: (out: T) => void; onError?: (error: Error) => void };

/**
 * A request that sends a password. The secret is never the mutation's variables (kept in the
 * mutation cache): it waits in a ref only while the request runs, and the finished mutation
 * is not kept in the cache (gcTime 0). `send` must take every secret from its argument.
 */
export function useSecretMutation<S, T>(send: (secret: S) => Promise<T>, handlers: Handlers<T>) {
  const secret = useRef<S | null>(null);
  const mutation = useMutation({
    mutationFn: () => {
      const value = secret.current;
      if (value === null) return Promise.reject(new Error("nothing to send"));
      return send(value);
    },
    onSettled: () => {
      secret.current = null;
    },
    gcTime: 0,
    ...handlers,
  });
  const submit = (value: S) => {
    secret.current = value;
    mutation.mutate();
  };
  return {
    submit,
    isPending: mutation.isPending,
    isSuccess: mutation.isSuccess,
    error: mutation.error,
    reset: mutation.reset,
  };
}
