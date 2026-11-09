import { ui } from "../i18n";
import { useEffect, useRef, useState } from "react";

type SiteRole = "reader" | "editor" | "admin";
type Account = {
  id: number;
  username: string;
  role: SiteRole;
  active: number;
  created_at: number;
  version: number;
};
type Overview = {
  counts: Record<string, number>;
  settings: { registration_open: boolean; version: number };
  sync: { pending: number; conflicts: number; last_success: number | null };
  user: { id: number; username: string };
};
type AuditEvent = {
  id: number;
  actor: string | null;
  action: string;
  target: string;
  detail: string;
  created_at: number;
};
const roles: Record<SiteRole, string> = {
  reader: ui.t("普通读者"),
  editor: ui.t("网站编辑者"),
  admin: ui.t("网站管理员"),
};
const actions: Record<string, string> = {
  "account.registered": ui.t("新用户注册"),
  "account.created": ui.t("创建账户"),
  "account.updated": ui.t("调整账户"),
  "registration.changed": ui.t("调整注册设置"),
  "account.promoted": ui.t("配置管理员"),
};
const date = (value: number | null) =>
  value
    ? new Date(value * 1000).toLocaleString(ui.locale, { hour12: false })
    : ui.t("暂无记录");

function describe(event: AuditEvent) {
  if (event.action !== "account.updated") return ui.t(event.detail);
  try {
    const info = JSON.parse(event.detail);
    return ui.t("{0} → {1}；{2}{3}", [roles[info.role_before as SiteRole], roles[info.role_after as SiteRole], info.active_after ? ui.t("启用") : ui.t("停用"), info.password_changed ? ui.t("；已重置密码") : ""]);
  } catch {
    return ui.t("账户信息已更新");
  }
}

export function AdminApp({ csrf }: { csrf: string }) {
  const [view, setView] = useState<"overview" | "users" | "audit">("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({ q: "", role: "", state: "" });
  const [roleFilter, setRoleFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Account | null>(null);
  const [draftRole, setDraftRole] = useState<SiteRole>("reader");
  const [draftActive, setDraftActive] = useState(true);
  const [modalError, setModalError] = useState("");
  const guard = useRef(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    const response = await fetch("/api/admin" + path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers:
        method === "GET"
          ? {}
          : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok)
      throw new Error(
        response.status === 403
          ? ui.t("当前账户没有管理员权限，或登录已过期。请重新登录。")
          : typeof data?.detail === "string"
            ? data.detail
            : ui.t("请求未完成，请稍后重试"),
      );
    return data;
  }

  useEffect(() => {
    let live = true;
    request<Overview>("/overview")
      .then((data) => {
        if (live) setOverview(data);
      })
      .catch((reason) => {
        if (live) setError(reason.message);
      });
    return () => {
      live = false;
    };
  }, [refresh]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    setTotal(0);
    setAccounts([]);
    setEvents([]);
    const query = new URLSearchParams({ ...filters, page: String(page) });
    const load =
      view === "users"
        ? request<{ users: Account[]; total: number }>("/users?" + query).then(
            (data) => {
              if (live) {
                setAccounts(data.users);
                setTotal(data.total);
              }
            },
          )
        : view === "audit"
          ? request<{ events: AuditEvent[]; total: number }>(
              "/audit?page=" + page,
            ).then((data) => {
              if (live) {
                setEvents(data.events);
                setTotal(data.total);
              }
            })
          : Promise.resolve();
    load
      .catch((reason) => {
        if (live) setError(reason.message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [view, filters, page, refresh]);

  useEffect(() => {
    if (editing) dialogRef.current?.showModal();
    else {
      dialogRef.current?.close();
      returnFocus.current?.focus();
    }
  }, [editing]);

  function changeView(next: typeof view) {
    if (guard.current) return;
    setView(next);
    setPage(1);
    setNotice("");
  }

  async function mutate(
    operation: () => Promise<unknown>,
    success: string,
    inModal = false,
  ) {
    if (guard.current) return;
    guard.current = true;
    setBusy(true);
    setError("");
    setModalError("");
    setNotice("");
    try {
      await operation();
      setEditing(null);
      setNotice(success);
      setRefresh((value) => value + 1);
    } catch (reason) {
      const message =
        reason instanceof Error ? reason.message : ui.t("操作失败，请稍后重试");
      if (inModal) setModalError(message);
      else setError(message);
    } finally {
      guard.current = false;
      setBusy(false);
    }
  }

  const cards = overview
    ? [
        {
          label: ui.t("注册账户"),
          value: overview.counts.users,
          note: ui.t("{0} 位已启用 · 近 7 天新增 {1}", [overview.counts.active_users, overview.counts.new_users]),
          icon: "01",
        },
        {
          label: ui.t("团队空间"),
          value: overview.counts.teams,
          note: ui.t("{0} 篇团队文档", [overview.counts.team_documents]),
          icon: "02",
        },
        {
          label: ui.t("公开笔记"),
          value: overview.counts.public_notes,
          note: ui.t("{0} 篇私人笔记单独授权", [overview.counts.private_notes]),
          icon: "03",
        },
        {
          label: ui.t("待处理同步"),
          value: overview.sync.pending + overview.sync.conflicts,
          note: ui.t("{0} 篇待同步 · {1} 篇冲突", [overview.sync.pending, overview.sync.conflicts]),
          icon: "04",
        },
      ]
    : [];

  return (
    <div className="admin-platform">
      <header className="platform-head">
        <div>
          <p className="platform-eyebrow">WIKITIDE / ADMINISTRATION</p>
          <h1>{ui.t("管理有序，协作有度。")}</h1>
          <p>{ui.t("你好，")}{overview?.user.username || ui.t("管理员")}{ui.t("。在这里照看账户、内容与协作。")}</p>
        </div>
        <a className="platform-link" href="/teams">{ui.t("进入团队空间 ↗")}</a>
      </header>
      <nav className="platform-tabs" aria-label={ui.t("管理员功能")}>
        {(
          [
            ["overview", ui.t("运行概览")],
            ["users", ui.t("账户管理")],
            ["audit", ui.t("操作记录")],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            aria-current={view === key ? "page" : undefined}
            disabled={busy}
            onClick={() => changeView(key)}
          >
            {label}
          </button>
        ))}
        <a href="/admin/permissions">{ui.t("笔记权限与同步 ↗")}</a>
      </nav>
      {notice && (
        <p className="platform-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <div className="platform-error" role="alert">
          {error}{" "}
          <button disabled={busy} onClick={() => setRefresh((n) => n + 1)}>{ui.t("重试")}</button>
          <a href="/login?next=/admin">{ui.t("登录")}</a>
        </div>
      )}
      {view === "overview" && (
        <>
          {!overview ? (
            <p role="status">{ui.t("正在读取运行概览…")}</p>
          ) : (
            <>
              <div className="platform-metrics">
                {cards.map((card) => (
                  <article key={card.icon}>
                    <div>
                      <span>{card.label}</span>
                      <small>{card.icon}</small>
                    </div>
                    <strong>{card.value.toLocaleString()}</strong>
                    <p>{card.note}</p>
                  </article>
                ))}
              </div>
              <div className="platform-grid">
                <section className="platform-panel registration-panel">
                  <p className="platform-eyebrow">WELCOME NEW MEMBERS</p>
                  <h2>{ui.t("为新的同伴，留一扇门。")}</h2>
                  <p>{ui.t("新注册用户从普通读者开始，创建自己的空间，或通过邀请加入团队。")}</p>
                  <div className="registration-state">
                    <span
                      className={
                        overview.settings.registration_open
                          ? "state-open"
                          : "state-closed"
                      }
                    >
                      {overview.settings.registration_open
                        ? ui.t("● 正在开放注册")
                        : ui.t("● 已暂停注册")}
                    </span>
                    <button
                      disabled={busy}
                      onClick={() =>
                        mutate(
                          () =>
                            request("/settings", "POST", {
                              registration_open:
                                !overview.settings.registration_open,
                              version: overview.settings.version,
                            }),
                          overview.settings.registration_open
                            ? ui.t("已暂停注册，已有账户仍可登录。")
                            : ui.t("已开放注册，新用户可创建账户。"),
                        )
                      }
                    >
                      {busy
                        ? ui.t("处理中…")
                        : overview.settings.registration_open
                          ? ui.t("暂停注册")
                          : ui.t("开放注册")}
                    </button>
                  </div>
                  <a className="platform-link" href="/register">{ui.t("查看注册入口 ↗")}</a>
                </section>
                <section className="platform-panel">
                  <p className="platform-eyebrow">KEEP THINGS IN ORDER</p>
                  <h2>{ui.t("管理，从清晰的边界开始。")}</h2>
                  <ul className="platform-shortcuts">
                    <li>
                      <button onClick={() => changeView("users")}>{ui.t("管理账户与网站角色 ")}<span>↗</span>
                      </button>
                      <p>{ui.t("启停账户，分配阅读、编辑与管理权限。")}</p>
                    </li>
                    <li>
                      <a href="/admin/permissions">{ui.t("处理笔记权限与同步 ")}<span>↗</span>
                      </a>
                      <p>{ui.t("最后成功同步：")}{date(overview.sync.last_success)}</p>
                    </li>
                    <li>
                      <button onClick={() => changeView("audit")}>{ui.t("查看最近的管理操作 ")}<span>↗</span>
                      </button>
                      <p>{ui.t("注册、权限与设置变更均有记录可查。")}</p>
                    </li>
                  </ul>
                </section>
              </div>
              <p className="platform-footnote">{ui.t("网站管理员可管理账户与个人笔记库。团队文档仍按空间成员身份授权，平台概览只展示数量。")}</p>
            </>
          )}
        </>
      )}
      {view === "users" && (
        <section className="platform-panel">
          <div className="platform-section-head">
            <div>
              <h2>{ui.t("每位同伴，各得其位。")}</h2>
              <p>{ui.t("调整网站角色与账户状态。停用或更改角色会使该账户的现有登录失效。")}</p>
            </div>
            <a className="platform-link" href="/admin/permissions">{ui.t("手动创建账户 ↗")}</a>
          </div>
          <form
            className="platform-filters"
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              setFilters({ q: search, role: roleFilter, state: stateFilter });
            }}
          >
            <label>{ui.t("用户名")}<input
                value={search}
                maxLength={64}
                placeholder={ui.t("搜索账户")}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <label>{ui.t("网站角色")}<select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
              >
                <option value="">{ui.t("全部角色")}</option>
                {Object.entries(roles).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>{ui.t("账户状态")}<select
                value={stateFilter}
                onChange={(e) => setStateFilter(e.target.value)}
              >
                <option value="">{ui.t("全部状态")}</option>
                <option value="active">{ui.t("已启用")}</option>
                <option value="disabled">{ui.t("已停用")}</option>
              </select>
            </label>
            <button type="submit" disabled={busy}>{ui.t("筛选")}</button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setRefresh((n) => n + 1)}
            >{ui.t("刷新")}</button>
          </form>
          <p className="platform-count">{ui.t("共 ")}{total}{ui.t(" 个账户")}</p>
          {loading ? (
            <p role="status">{ui.t("正在读取账户…")}</p>
          ) : (
            <div className="platform-accounts">
              {accounts.map((account) => (
                <article className="platform-account" key={account.id}>
                  <span className="account-avatar" aria-hidden="true">
                    {account.username.slice(0, 1).toUpperCase()}
                  </span>
                  <div className="account-identity">
                    <strong>
                      {account.username}
                      {account.id === overview?.user.id ? ui.t("（你）") : ""}
                    </strong>
                    <small>{ui.t("注册于 ")}{date(account.created_at)}</small>
                  </div>
                  <span className="role-tag">{roles[account.role]}</span>
                  <span
                    className={
                      account.active ? "account-enabled" : "account-disabled"
                    }
                  >
                    {account.active ? ui.t("已启用") : ui.t("已停用")}
                  </span>
                  <button
                    disabled={busy}
                    onClick={(event) => {
                      returnFocus.current = event.currentTarget;
                      setDraftRole(account.role);
                      setDraftActive(Boolean(account.active));
                      setModalError("");
                      setEditing(account);
                    }}
                  >{ui.t("管理")}<span className="sr-only"> {account.username}</span>
                  </button>
                </article>
              ))}
              {!accounts.length && <p>{ui.t("没有符合筛选条件的账户。")}</p>}
            </div>
          )}
        </section>
      )}
      {view === "audit" && (
        <section className="platform-panel">
          <h2>{ui.t("每一次调整，都有迹可循。")}</h2>
          <p>{ui.t("记录账户与注册设置变更，便于回看和排查。")}</p>
          {loading ? (
            <p role="status">{ui.t("正在读取操作记录…")}</p>
          ) : (
            <ol className="platform-audit">
              {events.map((event) => (
                <li key={event.id}>
                  <div>
                    <strong>{actions[event.action] || event.action}</strong>
                    <time>{date(event.created_at)}</time>
                  </div>
                  <p>
                    {event.actor || ui.t("系统维护")} · {event.target}
                  </p>
                  <small>{describe(event)}</small>
                </li>
              ))}
              {!events.length && <li>{ui.t("暂无操作记录。")}</li>}
            </ol>
          )}
        </section>
      )}
      {view !== "overview" && (
        <nav className="platform-pagination" aria-label={ui.t("分页")}>
          <button
            disabled={busy || loading || page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >{ui.t("上一页")}</button>
          <span>{ui.t("第 ")}{page}{ui.t(" 页 · 共 ")}{total}{ui.t(" 条")}</span>
          <button
            disabled={busy || loading || page * 20 >= total}
            onClick={() => setPage((p) => p + 1)}
          >{ui.t("下一页")}</button>
        </nav>
      )}
      <dialog
        className="platform-dialog"
        ref={dialogRef}
        aria-labelledby="account-dialog-title"
        onCancel={(event) => {
          event.preventDefault();
          if (!guard.current) setEditing(null);
        }}
      >
        {editing && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              mutate(
                () =>
                  request(`/users/${editing.id}`, "PATCH", {
                    role: draftRole,
                    active: draftActive,
                    version: editing.version,
                  }),
                ui.t("账户已更新，操作已记录。"),
                true,
              );
            }}
          >
            <p className="platform-eyebrow">ACCOUNT MANAGEMENT</p>
            <h2 id="account-dialog-title">{ui.t("管理 ")}{editing.username}</h2>
            {modalError && (
              <p className="platform-error" role="alert">
                {modalError}{ui.t("。输入已保留；如版本冲突，请关闭弹窗并刷新列表。")}</p>
            )}
            <label>{ui.t("网站角色")}<select
                value={draftRole}
                disabled={busy || editing.id === overview?.user.id}
                onChange={(e) => setDraftRole(e.target.value as SiteRole)}
              >
                {Object.entries(roles).map(([key, label]) => (
                  <option value={key} key={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="platform-checkbox">
              <input
                type="checkbox"
                checked={draftActive}
                disabled={busy || editing.id === overview?.user.id}
                onChange={(e) => setDraftActive(e.target.checked)}
              />{ui.t("启用此账户")}</label>
            <p className="platform-role-help">
              {draftRole === "admin"
                ? ui.t("网站管理员可以管理所有账户，并读取和编辑个人私密笔记。团队文档仍需加入空间。")
                : draftRole === "editor"
                  ? ui.t("网站编辑者只能编辑已获得编辑授权的个人笔记。团队权限单独设置。")
                  : ui.t("普通读者可阅读公开或已授权笔记，也可创建和加入团队。")}
            </p>
            {editing.id === overview?.user.id && (
              <p>{ui.t("为保留管理入口，当前管理员不能停用或降级自己。")}</p>
            )}
            <div className="platform-dialog-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(null)}
              >{ui.t("取消")}</button>
              <button
                className="platform-primary"
                type="submit"
                disabled={busy}
              >
                {busy ? ui.t("保存中…") : ui.t("保存变更")}
              </button>
            </div>
          </form>
        )}
      </dialog>
    </div>
  );
}
