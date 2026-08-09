import React, { createContext, useContext, useMemo, useState } from "react";

export type Language = "zh" | "en";

type LanguageState = {
  language: Language;
  setLanguage: (language: Language) => void;
  isZh: boolean;
};

const LanguageContext = createContext<LanguageState>({
  language: "zh",
  setLanguage: () => {},
  isZh: true,
});

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => (
    localStorage.getItem("ai_fitness_language") === "en" ? "en" : "zh"
  ));

  const value = useMemo<LanguageState>(() => ({
    language,
    isZh: language === "zh",
    setLanguage: (next) => {
      localStorage.setItem("ai_fitness_language", next);
      setLanguageState(next);
    },
  }), [language]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageState {
  return useContext(LanguageContext);
}
