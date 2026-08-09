import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "./AuthContext";
import { LanguageProvider } from "./LanguageContext";
import { LoginView } from "./LoginView";

describe("LoginView", () => {
  it("renders Chinese by default, clears legacy credentials, and switches to English", async () => {
    localStorage.setItem("ai_fitness_token", "legacy-script-readable-token");
    localStorage.setItem("ai_fitness_user", "legacy-user");
    localStorage.removeItem("ai_fitness_language");
    render(
      <LanguageProvider>
        <AuthProvider>
          <LoginView />
        </AuthProvider>
      </LanguageProvider>,
    );

    expect(screen.getByRole("heading", { name: "AI Fitness Coach" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "登录" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "注册" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "进入公开演示账号" })).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("ai_fitness_token")).toBeNull());
    expect(localStorage.getItem("ai_fitness_user")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "English" }));
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "Sign In" })).toHaveLength(2);
      expect(screen.getAllByRole("button", { name: "Create Account" })).toHaveLength(1);
    });
  });
});
