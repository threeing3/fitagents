import { useEffect, useState } from "react";
import { ArrowRight, FlaskConical, Languages, Moon, ShieldCheck, UserCircle } from "lucide-react";
import { AccountView } from "./AccountView";
import { AlgorithmLabView } from "./AlgorithmLabView";
import { useLanguage } from "./LanguageContext";

type Section = "general" | "account" | "development";
export function SettingsView({ initialSection = "general" }: { initialSection?: Section }) {
  const { isZh, language, setLanguage } = useLanguage();
  const [section, setSection] = useState<Section>(initialSection);
  const [openedAccount, setOpenedAccount] = useState(initialSection === "account");
  const [openedDevelopment, setOpenedDevelopment] = useState(initialSection === "development");
  const select = (next: Section) => {
    setSection(next);
    if (next === "account") setOpenedAccount(true);
    if (next === "development") setOpenedDevelopment(true);
  };
  useEffect(() => {
    setSection(initialSection);
    if (initialSection === "account") setOpenedAccount(true);
    if (initialSection === "development") setOpenedDevelopment(true);
  }, [initialSection]);
  const sections = [
    { id: "general" as const, label: isZh ? "通用设置" : "General", icon: <Languages size={17} /> },
    { id: "account" as const, label: isZh ? "账号资料" : "Account", icon: <UserCircle size={17} /> },
    { id: "development" as const, label: isZh ? "开发诊断" : "Developer diagnostics", icon: <FlaskConical size={17} /> },
  ];
  return <div className="settings-view">
    <header className="settings-header"><h2>{isZh ? "设置" : "Settings"}</h2><p>{isZh ? "管理显示偏好与账号资料。开发工具单独收纳，不打扰日常训练。" : "Manage display preferences and your account. Developer tools stay separate from everyday training."}</p></header>
    <div className="settings-tabs" role="tablist" aria-label={isZh ? "设置分类" : "Settings categories"} onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = sections.findIndex(item => item.id === section);
      const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + sections.length) % sections.length;
      select(sections[next].id);
      document.getElementById(`settings-${sections[next].id}-tab`)?.focus();
    }}>
      {sections.map(item => <button key={item.id} id={`settings-${item.id}-tab`} role="tab" aria-selected={section === item.id} aria-controls={`settings-${item.id}-panel`} tabIndex={section === item.id ? 0 : -1} onClick={() => select(item.id)}>{item.icon}{item.label}</button>)}
    </div>
    <div className="settings-content">
      <section id="settings-general-panel" role="tabpanel" aria-labelledby="settings-general-tab" hidden={section !== "general"}>
        <div className="settings-general-grid">
          <section className="settings-card"><h3>{isZh ? "显示偏好" : "Display preferences"}</h3>
            <div className="settings-row"><div><label htmlFor="settings-language">{isZh ? "界面语言" : "Interface language"}</label><p>{isZh ? "仅影响界面显示，保存在当前浏览器。" : "Changes interface text only. Saved in this browser."}</p></div><select id="settings-language" value={language} onChange={event => setLanguage(event.target.value === "en" ? "en" : "zh")}><option value="zh">简体中文</option><option value="en">English</option></select></div>
            <div className="settings-row"><div><strong>{isZh ? "外观" : "Appearance"}</strong><p>{isZh ? "当前版本采用统一深色界面。" : "This version uses a consistent dark interface."}</p></div><span className="settings-readonly"><Moon size={16} />{isZh ? "深色" : "Dark"}</span></div>
          </section>
          <section className="settings-card"><h3><ShieldCheck size={18} />{isZh ? "数据与操作边界" : "Data and action boundaries"}</h3>
            <p>{isZh ? "账号资料、训练记录、计划与长期记忆按登录账号隔离。" : "Account details, workouts, plans, and long-term memories are isolated by authenticated account."}</p>
            <p>{isZh ? "计划修改先生成草案，再由你明确批准。单次批准不授予未来自动修改权限，也不代表执行已经完成。" : "Review and explicitly approve each proposed plan change. Approval does not authorize future changes or confirm execution."}</p>
            <p className="settings-note">{isZh ? "这里说明现有行为，不是可关闭的安全开关。" : "These describe existing safeguards, not switches that disable them."}</p>
          </section>
          <section className="settings-card settings-developer-entry"><div><h3><FlaskConical size={18} />{isZh ? "开发诊断" : "Developer diagnostics"}</h3><p>{isZh ? "意图识别对照、离线评测与执行回放，仅在调试或项目复盘时使用。" : "Intent comparisons, offline evaluation, and execution replay for debugging and project review."}</p></div><button className="pill-btn" onClick={() => select("development")}>{isZh ? "打开开发诊断" : "Open diagnostics"}<ArrowRight size={16} /></button></section>
        </div>
      </section>
      {openedAccount && <section id="settings-account-panel" role="tabpanel" aria-labelledby="settings-account-tab" hidden={section !== "account"}><AccountView /></section>}
      {openedDevelopment && <section id="settings-development-panel" role="tabpanel" aria-labelledby="settings-development-tab" hidden={section !== "development"}><AlgorithmLabView /></section>}
    </div>
  </div>;
}
