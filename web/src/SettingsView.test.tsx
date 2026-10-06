import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SettingsView } from "./SettingsView";
import { LanguageProvider } from "./LanguageContext";

vi.mock("./AccountView", () => ({ AccountView: () => <input aria-label="账号草稿" /> }));
vi.mock("./AlgorithmLabView", () => ({ AlgorithmLabView: () => <div>诊断内容</div> }));

it("keeps diagnostics out of general settings until explicitly opened", () => {
  render(<SettingsView />);
  expect(screen.queryByText("诊断内容")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "打开开发诊断" }));
  expect(screen.getByRole("tab", { name: "开发诊断" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel", { name: "开发诊断" })).toBeVisible();
});

it("preserves unsaved account details across settings tabs with keyboard navigation", () => {
  render(<SettingsView initialSection="account" />);
  fireEvent.change(screen.getByLabelText("账号草稿"), { target: { value: "未提交昵称" } });
  fireEvent.keyDown(screen.getByRole("tab", { name: "账号资料" }), { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "开发诊断" })).toHaveFocus();
  fireEvent.click(screen.getByRole("tab", { name: "账号资料" }));
  expect(screen.getByLabelText("账号草稿")).toHaveValue("未提交昵称");
});

it("uses the existing persisted language preference rather than a cosmetic switch", () => {
  localStorage.setItem("ai_fitness_language", "zh");
  render(<LanguageProvider><SettingsView /></LanguageProvider>);
  fireEvent.change(screen.getByRole("combobox", { name: "界面语言" }), { target: { value: "en" } });
  expect(screen.getByRole("heading", { name: "Settings" })).toBeVisible();
  expect(localStorage.getItem("ai_fitness_language")).toBe("en");
  localStorage.setItem("ai_fitness_language", "zh");
});
