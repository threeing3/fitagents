import React, { useState } from "react";
import { Dumbbell, Mail, Lock, User, ArrowRight, AlertCircle } from "lucide-react";
import { useAuth } from "./AuthContext";

type Mode = "login" | "register";

export function LoginView() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!email.trim() || !password.trim()) {
      setError("Email and password are required.");
      return;
    }
    if (mode === "register" && !displayName.trim()) {
      setError("Display name is required.");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }

    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, displayName.trim());
      }
      // AuthContext will update user/token, triggering re-render to main app
    } catch (err: any) {
      const msg = String(err?.message || err || "Something went wrong");
      setError(msg.includes("duplicate key") || msg.includes("already exists")
        ? "An account with this email already exists. Try logging in."
        : msg);
    } finally {
      setBusy(false);
    }
  }

  function switchMode() {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError("");
    setEmail("");
    setPassword("");
    setDisplayName("");
  }

  return (
    <div className="login-root">
      <div className="login-card">
        {/* Brand */}
        <div className="login-brand">
          <Dumbbell size={28} />
          <h1>AI Fitness Coach</h1>
          <p>Your personal AI-powered training partner</p>
        </div>

        {/* Form */}
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
              <Lock size={16} className="input-icon" />
              <input
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>

            {mode === "register" && (
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
            )}
          </div>

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

        {/* Switch mode */}
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
