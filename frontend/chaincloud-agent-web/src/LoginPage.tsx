import { FormEvent, useState } from "react";
import { loginUser, registerUser } from "./api";
import type { AuthTokenResponse } from "./types";

interface LoginPageProps {
  onLoginSuccess: (auth: AuthTokenResponse) => void;
}

export default function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authDisplayName, setAuthDisplayName] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState("");

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAuthError("");

    const username = authUsername.trim();
    const password = authPassword;

    if (!username || !password) {
      setAuthError("请输入用户名和密码。");
      return;
    }

    setAuthLoading(true);
    try {
      const auth =
        authMode === "register"
          ? await registerUser({
              username,
              password,
              display_name: authDisplayName.trim() || undefined,
              metadata: { source: "chaincloud-agent-web" }
            })
          : await loginUser({ username, password });

      onLoginSuccess(auth);
      setAuthPassword("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : String(error));
    } finally {
      setAuthLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-logo">C</div>
          <h1>ChainCloud Agent</h1>
          <p>去中心化 AI Agent 平台</p>
        </div>

        <form className="login-form" onSubmit={handleAuthSubmit}>
          <div className="auth-tabs">
            <button
              type="button"
              className={authMode === "login" ? "auth-tab active" : "auth-tab"}
              onClick={() => { setAuthMode("login"); setAuthError(""); }}
            >
              登录
            </button>
            <button
              type="button"
              className={authMode === "register" ? "auth-tab active" : "auth-tab"}
              onClick={() => { setAuthMode("register"); setAuthError(""); }}
            >
              注册
            </button>
          </div>

          <label>
            Username
            <input
              value={authUsername}
              onChange={(event) => setAuthUsername(event.target.value)}
              placeholder="例如 demo"
              autoComplete="username"
            />
          </label>

          {authMode === "register" ? (
            <label>
              Display Name
              <input
                value={authDisplayName}
                onChange={(event) => setAuthDisplayName(event.target.value)}
                placeholder="可选，例如 Demo User"
                autoComplete="name"
              />
            </label>
          ) : null}

          <label>
            Password
            <input
              type="password"
              value={authPassword}
              onChange={(event) => setAuthPassword(event.target.value)}
              placeholder="至少 6 位"
              autoComplete={authMode === "login" ? "current-password" : "new-password"}
            />
          </label>

          {authError ? <p className="auth-error">{authError}</p> : null}

          <button className="primary full" disabled={authLoading}>
            {authLoading ? "处理中..." : authMode === "login" ? "登录" : "注册并登录"}
          </button>

          <p className="hint" style={{ textAlign: "center", marginTop: 12 }}>
            登录态保存在浏览器 localStorage。当前 MVP 用于本地演示和用户级命名，不等同于完整生产鉴权。
          </p>
        </form>
      </div>
    </div>
  );
}
