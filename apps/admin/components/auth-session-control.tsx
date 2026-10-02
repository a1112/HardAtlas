"use client";

import { useEffect, useState } from "react";

interface SessionState {
  authenticated: boolean;
  mode: "development" | "oidc";
}

export function AuthSessionControl() {
  const [session, setSession] = useState<SessionState>();

  useEffect(() => {
    void fetch("/apps/hardatlas-admin/api/auth/session", { cache: "no-store" })
      .then((response) => response.json() as Promise<SessionState>)
      .then(setSession);
  }, []);

  if (!session) return <span className="auth-session">身份检查中…</span>;
  if (!session.authenticated) {
    return (
      <a className="auth-login" href="/apps/hardatlas-admin/api/auth/login">
        使用 OIDC 登录
      </a>
    );
  }
  if (session.mode === "development") {
    return <span className="auth-session">本地开发身份 · admin</span>;
  }
  return (
    <form action="/apps/hardatlas-admin/api/auth/logout" method="post">
      <button className="secondary" type="submit">
        退出登录
      </button>
    </form>
  );
}
