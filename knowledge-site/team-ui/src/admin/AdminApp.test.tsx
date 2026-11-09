import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { AdminApp } from "./AdminApp";

const reply = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
let override:
  | ((
      url: string,
      options: RequestInit,
    ) => Response | Promise<Response> | undefined)
  | undefined;
let opened = true;

function setup() {
  const fetcher = vi.fn(
    async (input: string | URL | Request, options: RequestInit = {}) => {
      const url = String(input);
      const custom = override?.(url, options);
      if (custom) return custom;
      if (url.endsWith("/overview"))
        return reply({
          counts: {
            users: 2,
            active_users: 2,
            new_users: 1,
            teams: 1,
            team_documents: 3,
            public_notes: 10,
            private_notes: 1,
          },
          settings: { registration_open: opened, version: 4 },
          sync: { pending: 0, conflicts: 0, last_success: null },
          user: { id: 1, username: "shea" },
        });
      if (url.includes("/users?"))
        return reply({
          total: 2,
          users: [
            {
              id: 1,
              username: "shea",
              role: "admin",
              active: 1,
              version: 1,
              created_at: 1,
            },
            {
              id: 2,
              username: "bob",
              role: "reader",
              active: 1,
              version: 3,
              created_at: 1,
            },
          ],
        });
      if (url.includes("/audit?")) return reply({ total: 0, events: [] });
      return reply({ detail: "路由不存在" }, 404);
    },
  );
  vi.stubGlobal("fetch", fetcher);
  render(<AdminApp csrf="admin-csrf" />);
  return fetcher;
}

beforeEach(() => {
  override = undefined;
  opened = true;
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("protects the current admin and explains access before assigning an admin role", async () => {
  setup();
  await screen.findByText("注册账户");
  fireEvent.click(screen.getByRole("button", { name: "账户管理" }));
  fireEvent.click(await screen.findByRole("button", { name: "管理 shea" }));
  expect(
    (
      screen.getByLabelText("网站角色", {
        selector: "dialog select",
      }) as HTMLSelectElement
    ).disabled,
  ).toBe(true);
  expect(
    (screen.getByLabelText("启用此账户") as HTMLInputElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: "管理 bob" }));
  fireEvent.change(
    screen.getByLabelText("网站角色", { selector: "dialog select" }),
    { target: { value: "admin" } },
  );
  expect(screen.getByText(/网站管理员可以管理所有账户/)).toBeTruthy();
});

it("preserves permission choices on conflict and prevents duplicate mutations", async () => {
  let finish!: (value: Response) => void;
  let mutations = 0;
  override = (url, options) => {
    if (url === "/api/admin/users/2" && options.method === "PATCH") {
      mutations++;
      const body = JSON.parse(String(options.body));
      expect(body).toEqual({ role: "editor", active: false, version: 3 });
      expect((options.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
        "admin-csrf",
      );
      return new Promise((resolve) => {
        finish = resolve;
      });
    }
  };
  setup();
  await screen.findByText("注册账户");
  fireEvent.click(screen.getByRole("button", { name: "账户管理" }));
  fireEvent.click(await screen.findByRole("button", { name: "管理 bob" }));
  fireEvent.change(
    screen.getByLabelText("网站角色", { selector: "dialog select" }),
    { target: { value: "editor" } },
  );
  fireEvent.click(screen.getByLabelText("启用此账户"));
  const form = screen
    .getByRole("button", { name: "保存变更" })
    .closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(mutations).toBe(1);
  finish(reply({ detail: "账户已被其他管理员修改，请刷新后重试" }, 409));
  await screen.findByRole("alert");
  expect(
    (
      screen.getByLabelText("网站角色", {
        selector: "dialog select",
      }) as HTMLSelectElement
    ).value,
  ).toBe("editor");
  expect(
    (screen.getByLabelText("启用此账户") as HTMLInputElement).checked,
  ).toBe(false);
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("refreshes authoritative registration state only after a successful change", async () => {
  override = (url, options) => {
    if (url === "/api/admin/settings" && options.method === "POST") {
      expect(JSON.parse(String(options.body))).toEqual({
        registration_open: false,
        version: 4,
      });
      opened = false;
      return reply({ registration_open: false, version: 5 });
    }
  };
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "暂停注册" }));
  await screen.findByText("已暂停注册，已有账户仍可登录。");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "开放注册" })).toBeTruthy(),
  );
});
