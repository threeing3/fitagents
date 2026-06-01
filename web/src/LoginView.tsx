import React, { useState } from "react";
import { Dumbbell, Mail, Lock, User, ArrowRight, AlertCircle, AtSign } from "lucide-react";
import { useAuth } from "./AuthContext";

type Mode = "login" | "register";

export function LoginView() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [identifier, setIdentifier] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (mode === "login" && (!identifier.trim() || !password.trim())) {
      setError("Email/username and password are required.");
      return;
    }
    if (mode === "register" && (!email.trim() || !password.trim() || !displayName.trim())) {
      setError("Email, display name, and password are required.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }

    setBusy(true);
    try {
      if (mode === "login") {
        await login(identifier.trim(), password);
      } else {
        await register(email.trim(), password, displayName.trim(), username.trim() || undefined);
      }
    } catch (err: any) {
      const msg = String(err?.message || err || "Something went wrong");
      setError(msg.includes("already exists")
        ? "This email or username is already taken. Try signing in or choose another one."
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
  }

  return (
    <div className="login-root">
      <div className="login-card">
        <div className="login-brand">
          <Dumbbell size={28} />
          <h1>AI Fitness Coach</h1>
          <p>One account, one private coaching memory space</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-tabs">
            <button
              type="button"
              className={`login-tab ${mode === "login" ? "active" : ""}`}
              onClick={() => setMode("login")}
            >
              Sign In
            </button>
            <button
              type="button"
              className={`login-tab ${mode === "register" ? "active" : ""}`}
              onClick={() => setMode("register")}
            >
              Create Account
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
                  placeholder="Email or username"
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
                    placeholder="Email address"
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
                    placeholder="Display name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    autoComplete="name"
                  />
                </div>

                <div className="input-group">
                  <AtSign size={16} className="input-icon" />
                  <input
                    type="text"
                    placeholder="Username, optional"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
                  />
                </div>
              </>
            )}

            <div className="input-group">
              <Lock size={16} className="input-icon" />
              <input
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>
          </div>

          <p className="login-hint">
            {mode === "login"
              ? "You can sign in with either your email address or username."
              : "Your coach history, memory, plans, and logs are isolated under this account."}
          </p>

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? (
              <span className="login-spinner" />
            ) : (
              <>
                <span>{mode === "login" ? "Sign In" : "Create Account"}</span>
                <ArrowRight size={18} />
              </>
            )}
          </button>
        </form>

        <p className="login-switch">
          {mode === "login" ? (
            <>
              Don&apos;t have an account?{" "}
              <button type="button" onClick={switchMode}>Sign up</button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" onClick={switchMode}>Sign in</button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
