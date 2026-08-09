import React, { useMemo, useState } from "react";
import { AtSign, CheckCircle2, Image, Mail, ShieldCheck, User } from "lucide-react";
import { useAuth } from "./AuthContext";
import { useLanguage } from "./LanguageContext";

export function AccountView() {
  const auth = useAuth();
  const { isZh } = useLanguage();
  const user = auth.user;
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [username, setUsername] = useState(user?.username || "");
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url || "");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const initials = useMemo(() => {
    const source = displayName || username || user?.email || "U";
    return source.trim().slice(0, 2).toUpperCase();
  }, [displayName, username, user?.email]);

  if (!user) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setNotice("");
    setError("");
    if (!displayName.trim()) {
      setError(isZh ? "显示名称不能为空。" : "Display name is required.");
      return;
    }
    setBusy(true);
    try {
      await auth.updateProfile({
        display_name: displayName.trim(),
        username: username.trim(),
        avatar_url: avatarUrl.trim(),
      });
      setNotice(isZh ? "账号资料已保存。" : "Account profile saved.");
    } catch (err: any) {
      setError(String(err?.message || err || (isZh ? "保存账号资料失败。" : "Failed to save account profile.")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account-view">
      <section className="account-header">
        <div className="account-avatar large">
          {avatarUrl ? <img src={avatarUrl} alt="" /> : <span>{initials}</span>}
        </div>
        <div>
          <h2>{user.display_name}</h2>
          <p>{user.email}</p>
        </div>
      </section>

      <section className="account-grid">
        <form className="account-panel" onSubmit={save}>
          <div className="account-panel-title">
            <User size={18} />
            <span>{isZh ? "账号资料" : "Account profile"}</span>
          </div>

          {notice && (
            <div className="account-notice success">
              <CheckCircle2 size={16} />
              <span>{notice}</span>
            </div>
          )}
          {error && <div className="account-notice error">{error}</div>}

          <label className="account-field">
            <span>{isZh ? "显示名称" : "Display name"}</span>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </label>

          <label className="account-field">
            <span>{isZh ? "用户名" : "Username"}</span>
            <div className="account-input-icon">
              <AtSign size={15} />
              <input value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
          </label>

          <label className="account-field">
            <span>{isZh ? "头像地址" : "Avatar URL"}</span>
            <div className="account-input-icon">
              <Image size={15} />
              <input
                value={avatarUrl}
                onChange={(e) => setAvatarUrl(e.target.value)}
                placeholder="https://..."
              />
            </div>
          </label>

          <button className="account-save" disabled={busy} type="submit">
            {busy ? (isZh ? "保存中…" : "Saving...") : (isZh ? "保存账号" : "Save account")}
          </button>
        </form>

        <aside className="account-panel">
          <div className="account-panel-title">
            <ShieldCheck size={18} />
            <span>{isZh ? "数据隔离" : "Data isolation"}</span>
          </div>
          <div className="account-fact">
            <Mail size={16} />
            <div>
              <strong>{isZh ? "邮箱" : "Email"}</strong>
              <span>{user.email}</span>
            </div>
          </div>
          <div className="account-fact">
            <AtSign size={16} />
            <div>
              <strong>{isZh ? "登录名" : "Login name"}</strong>
              <span>{user.username || (isZh ? "未设置" : "Not set")}</span>
            </div>
          </div>
          <p className="account-copy">
            {isZh
              ? "对话、长期记忆、计划、日志和运行轨迹都按已认证用户隔离，其他账号无法读取。"
              : "Chats, long-term memories, plans, logs, traces, and dashboard data are isolated by your authenticated user id."}
          </p>
        </aside>
      </section>
    </div>
  );
}
