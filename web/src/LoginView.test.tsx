import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "./AuthContext";
import { LoginView } from "./LoginView";

describe("LoginView", () => {
  it("renders the sign-in entry without contacting the API", () => {
    render(
      <AuthProvider>
        <LoginView />
      </AuthProvider>,
    );

    expect(screen.getByRole("heading", { name: "AI Fitness Coach" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Sign In" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Create Account" })).toBeInTheDocument();
  });
});
