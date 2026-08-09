import React, { useState } from "react";
import { Dumbbell, Mail, Lock, User, ArrowRight, AlertCircle, AtSign, KeyRound, Languages, PlayCircle } from "lucide-react";
import { useAuth } from "./AuthContext";
import { useLanguage } from "./LanguageContext";

type Mode = "login" | "register";

export function LoginView() {
  const { login, register, loginDemo } = useAuth();
  const { isZh, language, setLanguage } = useLanguage();
  const [mode, setMode] = useState<Mode>("login");
  const [identifier, setIdentifier] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (mode === "login" && (!identifier.trim() || !password.trim())) {
      setError(isZh ? "请输入邮箱或用户名和密码。" : "Email/username and password are required.");
      return;
    }
    if (mode === "register" && (!email.trim() || !password.trim() || !displayName.trim())) {
      setError(isZh ? "请输入邮箱、显示名称和密码。" : "Email, display name, and password are required.");
      return;
    }
    if (mode === "register" && password.length < 10) {
      setError(isZh ? "新密码至少需要 10 位。" : "New passwords must be at least 10 characters.");
      return;
    }

    setBusy(true);
    try {
      if (mode === "login") {
        await login(identifier.trim(), password);
      } else {
        await register(email.trim(), password, displayName.trim(), username.trim() || undefined, inviteCode.trim() || undefined);
      }
    } catch (err: any) {
      const msg = String(err?.message || err || (isZh ? "请求失败，请重试。" : "Something went wrong"));
      setError(msg.includes("already exists")
        ? (isZh ? "邮箱或用户名已被使用，请登录或更换。" : "This email or username is already taken. Try signing in or choose another one.")
        : msg);
    } finally {
      setBusy(false);
    }
  }

  function switchMode() {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError("");
    setIdentifier("");
    setEmail("");
    setUsername("");
    setPassword("");
    setDisplayName("");
    setInviteCode("");
  }

  async function handleDemoLogin() {
    setBusy(true);
    setError("");
    try {
      await loginDemo();
    } catch (err: any) {
      setError(String(err?.message || (isZh ? "演示账号暂不可用。" : "Demo account is unavailable.")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-root">
      <div className="login-card">
        <button
          type="button"
          className="language-toggle"
          onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
        >
          <Languages size={15} /> {isZh ? "English" : "中文"}
        </button>
        <div className="login-brand">
          <Dumbbell size={28} />
          <h1>AI Fitness Coach</h1>
          <p>{isZh ? "一个账号，一套隔离的私人教练记忆" : "One account, one private coaching memory space"}</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-tabs">
            <button
              type="button"
              className={`login-tab ${mode === "login" ? "active" : ""}`}
              onClick={() => setMode("login")}
            >
              {isZh ? "登录" : "Sign In"}
            </button>
            <button
              type="button"
              className={`login-tab ${mode === "register" ? "active" : ""}`}
              onClick={() => setMode("register")}
            >
              {isZh ? "注册" : "Create Account"}
            </button>
          </div>

          {error && (
            <div className="login-error">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          <div className="login-fields">
            {mode === "login" ? (
              <div className="input-group">
                <AtSign size={16} className="input-icon" />
                <input
                  type="text"
                  placeholder={isZh ? "邮箱或用户名" : "Email or username"}
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  autoComplete="username"
                  autoFocus
                />
              </div>
            ) : (
              <>
                <div className="input-group">
                  <Mail size={16} className="input-icon" />
                  <input
                    type="email"
                    placeholder={isZh ? "邮箱" : "Email address"}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoComplete="email"
                    autoFocus
                  />
                </div>

                <div className="input-group">
                  <User size={16} className="input-icon" />
                  <input
                    type="text"
                    placeholder={isZh ? "显示名称" : "Display name"}
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    autoComplete="name"
                  />
                </div>

                <div className="input-group">
                  <AtSign size={16} className="input-icon" />
                  <input
                    type="text"
                    placeholder={isZh ? "用户名（可选）" : "Username, optional"}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
                  />
                </div>

                <div className="input-group">
                  <KeyRound size={16} className="input-icon" />
                  <input
                    type="text"
                    placeholder={isZh ? "邀请码" : "Invite code"}
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value)}
                    autoComplete="off"
                  />
                </div>
              </>
            )}

            <div className="input-group">
              <Lock size={16} className="input-icon" />
              <input
                type="password"
                placeholder={isZh ? "密码" : "Password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>
          </div>

          <p className="login-hint">
            {mode === "login"
              ? (isZh ? "可使用邮箱或用户名登录。" : "You can sign in with either your email address or username.")
              : (isZh ? "训练记录、记忆和计划会隔离在你的账号下。" : "Your coach history, memory, plans, and logs are isolated under this account.")}
          </p>

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? (
              <span className="login-spinner" />
            ) : (
              <>
                <span>{mode === "login" ? (isZh ? "登录" : "Sign In") : (isZh ? "创建账号" : "Create Account")}</span>
                <ArrowRight size={18} />
              </>
            )}
          </button>
        </form>

        <button type="button" className="demo-login" disabled={busy} onClick={handleDemoLogin}>
          <PlayCircle size={17} />
          {isZh ? "进入公开演示账号" : "Enter public demo"}
        </button>

        {busy && (
          <p className="cold-start-copy">
            {isZh ? "免费服务可能正在唤醒，首次请求可能需要约一分钟。" : "The free service may be waking up; the first request can take about a minute."}
          </p>
        )}

        <div className="public-boundaries">
          <p>{isZh ? "隐私：请勿在公开演示中输入真实姓名、联系方式或医疗记录。" : "Privacy: do not enter real names, contact details, or medical records in the public demo."}</p>
          <p>{isZh ? "医疗边界：本项目提供一般健身信息，不能替代医生诊断或急救。" : "Medical boundary: this project provides general fitness information, not diagnosis or emergency care."}</p>
        </div>

        <p className="login-switch">
          {mode === "login" ? (
            <>
              {isZh ? "还没有账号？" : "Don't have an account?"}{" "}
              <button type="button" onClick={switchMode}>{isZh ? "注册" : "Sign up"}</button>
            </>
          ) : (
            <>
              {isZh ? "已经有账号？" : "Already have an account?"}{" "}
              <button type="button" onClick={switchMode}>{isZh ? "登录" : "Sign in"}</button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
