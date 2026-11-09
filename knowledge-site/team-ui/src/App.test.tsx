import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "./App";
import { type Doc, type Team, type TeamRole } from "./api";

// jsdom has no layout engine; CodeMirror's scheduled selection measurement
// still needs the Range geometry APIs during these state/HTTP tests.
if (!Range.prototype.getClientRects) Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = () => new DOMRect();

const reply = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const firstDoc: Doc = {
  id: 10,
  team_id: 1,
  title: "协作约定",
  body: "原始正文",
  html: "<p>原始正文</p>",
  version: 1,
  updated_at: 1,
};
let teams: Team[], activeDoc: Doc;
let override:
  | ((
      url: string,
      options: RequestInit,
    ) => Response | Promise<Response> | undefined)
  | undefined;

function setup(role: TeamRole = "owner", extraTeams: Team[] = []) {
  teams = [
    {
      id: 1,
      name: "产品研发",
      description: "共同整理",
      role,
      version: 4,
      member_count: 2,
      document_count: 1,
    },
    ...extraTeams,
  ];
  activeDoc = { ...firstDoc };
  const fetcher = vi.fn(
    async (input: string | URL | Request, options: RequestInit = {}) => {
      const url = String(input);
      const custom = override?.(url, options);
      if (custom) return custom;
      if (url === "/api/teams") return reply({ teams });
      if (url === "/api/teams/invitations") return reply({ invitations: [] });
      const id = Number(url.match(/^\/api\/teams\/(\d+)/)?.[1]);
      if (url === `/api/teams/${id}`)
        return reply({
          team: teams.find((team) => team.id === id),
          members: [
            { user_id: 1, username: "alice", active: 1, role },
            { user_id: 2, username: "bob", active: 1, role: "reader" },
          ],
          invitations: [],
          user_id: 1,
        });
      if (url.includes("/documents?"))
        return reply({
          documents: id === 1 ? [activeDoc] : [],
          total: id === 1 ? 1 : 0,
          page: 1,
          page_size: 20,
        });
      if (url.endsWith("/events")) return reply({ events: [] });
      if (url === "/api/teams/1/documents/10") return reply(activeDoc);
      return reply({ detail: "测试路由未匹配" }, 404);
    },
  );
  vi.stubGlobal("fetch", fetcher);
  render(<App csrf="csrf-for-test" username="alice" />);
  return fetcher;
}

beforeEach(() => {
  override = undefined;
  window.history.replaceState(null, "", "/teams");
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("团队空间交互", () => {
  it("reader sees documents but no editing or membership management controls", async () => {
    setup("reader");
    await screen.findByRole("heading", { name: "产品研发" });
    expect(screen.queryByRole("button", { name: "新建文档" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /协作约定/ }));
    await screen.findByText("原始正文");
    expect(screen.queryByRole("button", { name: "编辑文档" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "成员与邀请" }));
    expect(screen.queryByRole("button", { name: "邀请成员" })).toBeNull();
    expect(screen.queryByRole("button", { name: "移除" })).toBeNull();
  });

  it("keeps unsaved input on conflict and requires explicit comparison before retrying", async () => {
    let saves = 0;
    override = (url, options) => {
      if (url === "/api/teams/1/documents/10" && options.method === "PUT") {
        saves++;
        if (saves === 1) {
          activeDoc = {
            ...activeDoc,
            version: 2,
            body: "另一位成员的新版",
            html: "<p>另一位成员的新版</p>",
          };
          return reply({ detail: "版本冲突，输入已保留" }, 409);
        }
        const body = JSON.parse(String(options.body));
        expect(body.version).toBe(2);
        expect(body.body).toBe("我的修改已保留");
        expect(
          (options.headers as Record<string, string>)["X-CSRF-Token"],
        ).toBe("csrf-for-test");
        activeDoc = {
          ...activeDoc,
          ...body,
          version: 3,
          html: "<p>我的修改已保留</p>",
        };
        return reply(activeDoc);
      }
    };
    setup();
    await screen.findByRole("heading", { name: "产品研发" });
    fireEvent.click(screen.getByRole("button", { name: /协作约定/ }));
    fireEvent.click(await screen.findByRole("button", { name: "编辑文档" }));
    fireEvent.change(screen.getByLabelText("Markdown 正文"), {
      target: { value: "我的修改已保留" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存文档" }));
    await screen.findByText("另一位成员的新版");
    expect(
      (screen.getByLabelText("Markdown 正文") as HTMLTextAreaElement).value,
    ).toBe("我的修改已保留");
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "保存文档" }) as HTMLButtonElement)
          .disabled,
      ).toBe(true),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "我已对照，基于最新版本继续编辑" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存文档" }));
    await screen.findByText("文档已保存，空间成员可立即阅读。");
    expect(saves).toBe(2);
  });

  it("preserves create form values on failure and guards repeated submits", async () => {
    let done!: (value: Response) => void;
    let creates = 0;
    override = (url, options) => {
      if (url === "/api/teams" && options.method === "POST") {
        creates++;
        return new Promise<Response>((resolve) => {
          done = resolve;
        });
      }
    };
    setup();
    await screen.findByRole("heading", { name: "产品研发" });
    fireEvent.click(screen.getByRole("button", { name: "创建空间" }));
    fireEvent.change(screen.getByLabelText("空间名称"), {
      target: { value: "未保存的空间名" },
    });
    const form = screen.getByLabelText("空间名称").closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(creates).toBe(1);
    done(reply({ detail: "服务暂不可用" }, 503));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("空间名称") as HTMLInputElement).value).toBe(
      "未保存的空间名",
    );
    expect(screen.getByRole("dialog", { name: "创建团队空间" })).toBeTruthy();
  });

  it("ignores a document response that arrives after switching to another team", async () => {
    let finish!: (value: Response) => void;
    override = (url, options) => {
      if (url === "/api/teams/1/documents/10" && options.method === "GET")
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
    };
    setup("owner", [
      {
        id: 2,
        name: "另一空间",
        description: "",
        role: "reader",
        version: 1,
        member_count: 1,
        document_count: 0,
      },
    ]);
    await screen.findByRole("heading", { name: "产品研发" });
    fireEvent.click(screen.getByRole("button", { name: /协作约定/ }));
    fireEvent.click(screen.getByRole("button", { name: /另一空间/ }));
    await screen.findByRole("heading", { name: "另一空间" });
    finish(
      reply({
        ...firstDoc,
        body: "过期响应中的文档",
        html: "<p>过期响应中的文档</p>",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "另一空间" })).toBeTruthy(),
    );
    expect(screen.queryByText("过期响应中的文档")).toBeNull();
  });
});
