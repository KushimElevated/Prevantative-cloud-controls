"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useApi } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { ApiError } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

type DemoUser = { user_id: string; username: string; display_name: string; team: string;
                  grants: { role: string; scope_ids: string[] }[] };

function LoginInner() {
  const { data, error, loading } = useApi<{ items: DemoUser[]; warning: string }>("/auth/demo-users");
  const { login } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [err, setErr] = useState<ApiError | null>(null);
  const next = params.get("next");
  // Without an explicit next, "/" picks Classic or the AI Workspace from the saved preference.
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">Choose a local demo identity</h1>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
        Demo identities exist only in explicit local demo mode. Roles and scopes come from the server; production
        requires a real identity provider (OIDC / Microsoft Entra ID), which is not implemented yet.
      </p>
      {loading && <Loading />}
      <ErrorBox error={error ?? err} />
      <ul className="mt-6 grid gap-3 sm:grid-cols-2">
        {data?.items.map((u) => (
          <li key={u.user_id}>
            <button
              data-testid={`login-${u.username}`}
              onClick={async () => {
                try {
                  await login(u.username);
                  router.push(target);
                } catch (e) {
                  setErr(e as ApiError);
                }
              }}
              className="w-full rounded-lg border border-slate-200 bg-white p-4 text-left hover:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-500 dark:border-slate-700 dark:bg-slate-900"
            >
              <div className="font-medium">{u.display_name} <span className="text-sm text-slate-500">@{u.username}</span></div>
              <div className="text-xs text-slate-500">{u.team}</div>
              <ul className="mt-2 text-xs text-slate-700 dark:text-slate-300">
                {u.grants.map((g) => (
                  <li key={g.role}>{g.role} <span className="text-slate-500">on {g.scope_ids.join(", ").replace("*", "all scopes")}</span></li>
                ))}
              </ul>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<Loading />}>
      <LoginInner />
    </Suspense>
  );
}
