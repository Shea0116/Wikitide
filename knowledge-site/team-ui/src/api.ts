import { ui } from "./i18n";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function createApi(csrf: string) {
  return async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    const response = await fetch("/api/teams" + path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers:
        method === "GET"
          ? {}
          : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) {
      const message =
        typeof result?.detail === "string"
          ? result.detail
          : ui.t("请求未完成，请稍后重试");
      throw new ApiError(
        response.status,
        response.status === 401
          ? ui.t("登录已过期，请重新登录后继续；当前输入仍保留。")
          : message,
      );
    }
    return result as T;
  };
}

export type TeamRole = "owner" | "admin" | "editor" | "reader";
export const roleNames: Record<TeamRole, string> = {
  owner: ui.t("所有者"),
  admin: ui.t("管理员"),
  editor: ui.t("编辑者"),
  reader: ui.t("读者"),
};
export type Team = {
  id: number;
  name: string;
  description: string;
  role: TeamRole;
  version: number;
  member_count: number;
  document_count: number;
};
export type Member = {
  user_id: number;
  username: string;
  active: number;
  role: TeamRole;
};
export type Invitation = {
  id: number;
  team_id?: number;
  team_name?: string;
  username?: string;
  invited_by?: string;
  role: TeamRole;
  status: string;
  expires_at: number;
};
export type Detail = {
  team: Team;
  members: Member[];
  invitations: Invitation[];
  user_id: number;
};
export type Doc = {
  id: number;
  team_id: number;
  title: string;
  body: string;
  html: string;
  version: number;
  updated_at: number;
  pdf?: { filename: string; size: number; note_format: 'pdf' | 'markdown'; url: string; download_url: string } | null;
  is_pdf?: boolean;
};
export type DocList = {
  documents: Pick<Doc, "id" | "title" | "version" | "updated_at" | "is_pdf">[];
  total: number;
  page: number;
  page_size: number;
};
export type TeamEvent = {
  id: number;
  detail: string;
  actor: string;
  created_at: number;
};
export type Revision = {
  version: number;
  title: string;
  actor: string;
  created_at: number;
};
