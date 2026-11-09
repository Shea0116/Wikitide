import { ui } from "./i18n";
import '../../static/pdf-notes.css';
import { HistoryDiff } from "./VersionDiff";
import { SharedEditor } from './SharedEditor';
import { TeamDraftTools } from './TeamDraftTools';
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ApiError,
  createApi,
  roleNames,
  type Team,
  type Detail,
  type Doc,
  type DocList,
  type Invitation,
  type Member,
  type TeamEvent,
  type Revision,
  type TeamRole,
} from "./api";

const date = (value: number) =>
  new Date(value * 1000).toLocaleString(ui.locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
const manages = (role: TeamRole) => role === "owner" || role === "admin";
const writes = (role: TeamRole) => role !== "reader";
type Draft = { id?: number; title: string; body: string; version: number };

function Icon({
  name,
}: {
  name: "plus" | "people" | "file" | "arrow" | "search" | "close" | "bell";
}) {
  const paths = {
    plus: "M12 5v14M5 12h14",
    people:
      "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M18 8a3 3 0 0 1 0 6M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    file: "M14 2H6v20h12V6l-4-4ZM14 2v5h5M8 12h7M8 16h7",
    arrow: "M5 12h14M13 6l6 6-6 6",
    search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    close: "M6 6l12 12M6 18 18 6",
    bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4",
  };
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      className="team-modal"
      ref={ref}
      onCancel={close}
      aria-label={title}
    >
      <header>
        <h2>{title}</h2>
        <button
          className="icon-button"
          type="button"
          onClick={close}
          aria-label={ui.t("关闭弹窗")}
        >
          <Icon name="close" />
        </button>
      </header>
      {children}
    </dialog>
  );
}

function MemberRow({
  member,
  detail,
  busy,
  act,
}: {
  member: Member;
  detail: Detail;
  busy: boolean;
  act: (id: number, action: string, role?: string) => void;
}) {
  const [role, setRole] = useState(member.role);
  useEffect(() => setRole(member.role), [member.role]);
  const canManage =
    member.user_id !== detail.user_id &&
    member.role !== "owner" &&
    manages(detail.team.role) &&
    (detail.team.role === "owner" || member.role !== "admin");
  return (
    <li className="member-row">
      <span className="member-avatar">
        {member.username.slice(0, 1).toUpperCase()}
      </span>
      <div className="member-name">
        <strong>
          {member.username}
          {member.user_id === detail.user_id ? ui.t("（你）") : ""}
        </strong>
        <small>{member.active ? ui.t("已加入空间") : ui.t("账户已停用")}</small>
      </div>
      {canManage ? (
        <>
          <label className="sr-only" htmlFor={"role-" + member.user_id}>
            {member.username}{ui.t(" 的角色")}</label>
          <select
            id={"role-" + member.user_id}
            value={role}
            onChange={(e) => setRole(e.target.value as TeamRole)}
            disabled={busy}
          >
            {detail.team.role === "owner" && (
              <option value="admin">{ui.t("管理员")}</option>
            )}
            <option value="editor">{ui.t("编辑者")}</option>
            <option value="reader">{ui.t("读者")}</option>
          </select>
          <button
            disabled={busy || role === member.role}
            onClick={() => act(member.user_id, "role", role)}
          >{ui.t("保存角色")}</button>
          <button
            className="text-danger"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  ui.t("将 {0} 移出空间？对方将失去该空间的访问权限。", [member.username]),
                )
              )
                act(member.user_id, "remove");
            }}
          >{ui.t("移除")}</button>
        </>
      ) : (
        <span className="role-tag">{roleNames[member.role]}</span>
      )}
    </li>
  );
}

export function App({ csrf, username }: { csrf: string; username: string }) {
  const api = useMemo(() => createApi(csrf), [csrf]);
  const [teams, setTeams] = useState<Team[]>([]),
    [inbox, setInbox] = useState<Invitation[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null),
    [list, setList] = useState<DocList | null>(null),
    [events, setEvents] = useState<TeamEvent[]>([]);
  const [tab, setTab] = useState<"documents" | "members" | "activity">(
    "documents",
  );
  const [doc, setDoc] = useState<Doc | null>(null),
    [draft, setDraft] = useState<Draft | null>(null),
    [preview, setPreview] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Doc | null>(null),
    [revisions, setRevisions] = useState<Revision[] | null>(null);
  const [query, setQuery] = useState(""),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [modal, setModal] = useState<
    "create" | "invite" | "inbox" | "transfer" | null
  >(null);
  const [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [invitee, setInvitee] = useState(""),
    [inviteRole, setInviteRole] = useState("editor"),
    [transferTo, setTransferTo] = useState("");
  const selected = useRef<number | null>(null),
    epoch = useRef(0),
    documentEpoch = useRef(0),
    submitting = useRef(false);
  const dirty =
    !!draft &&
    (draft.title !== (doc?.title || "") || draft.body !== (doc?.body || ""));
  const canNavigate = () =>
    !dirty || window.confirm(ui.t("有尚未保存的修改，确定放弃并离开吗？"));
  useEffect(() => window.wikiTideNative?.register('team-editor', () => ({dirty,busy:busy||loading})), [dirty,busy,loading]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function showError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : ui.t("请求未完成，请重试"));
  }
  function closeModal() {
    if (!submitting.current) {
      setModal(null);
      setError("");
    }
  }
  function openModal(value: typeof modal) {
    if (busy) return;
    setError("");
    setNotice("");
    if (value === "invite") setInviteRole("editor");
    if (value === "transfer") setTransferTo("");
    setModal(value);
  }

  async function loadOverview() {
    const [spaces, invitations] = await Promise.all([
      api<{ teams: Team[] }>(""),
      api<{ invitations: Invitation[] }>("/invitations"),
    ]);
    setTeams(spaces.teams);
    setInbox(invitations.invitations);
    return spaces.teams;
  }
  async function loadTeam(id: number, search = "", page = 1) {
    const turn = ++epoch.current;
    selected.current = id;
    setLoading(true);
    try {
      const [state, documents, activity] = await Promise.all([
        api<Detail>(`/${id}`),
        api<DocList>(
          `/${id}/documents?${new URLSearchParams({ q: search, page: String(page) })}`,
        ),
        api<{ events: TeamEvent[] }>(`/${id}/events`),
      ]);
      if (turn !== epoch.current) return;
      setDetail(state);
      setList(documents);
      setEvents(activity.events);
      window.history.replaceState(null, "", `/teams?space=${id}`);
    } catch (reason) {
      if (turn === epoch.current) {
        setDetail(null);
        setList(null);
        setDoc(null);
        setDraft(null);
        showError(reason);
      }
    } finally {
      if (turn === epoch.current) setLoading(false);
    }
  }
  async function refresh() {
    await loadOverview();
    if (selected.current) await loadTeam(selected.current);
  }
  useEffect(() => {
    let alive = true;
    const requestedDocument = Number(new URLSearchParams(window.location.search).get("document"));
    loadOverview()
      .then(async (spaces) => {
        if (!alive) return;
        const requested = Number(
          new URLSearchParams(window.location.search).get("space"),
        );
        const id = spaces.some((team) => team.id === requested)
          ? requested
          : spaces[0]?.id;
        if (id) {
          await loadTeam(id);
          if (requestedDocument > 0 && alive) {
            const opened = await api<Doc>(`/${id}/documents/${requestedDocument}`);
            if (alive && selected.current === id) setDoc(opened);
          }
        }
        else setLoading(false);
      })
      .catch((reason) => {
        if (alive) {
          showError(reason);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
      epoch.current++;
      documentEpoch.current++;
    };
  }, [api]);

  async function action(work: () => Promise<void>) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (reason) {
      showError(reason);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  async function switchTeam(id: number) {
    if (busy || !canNavigate()) return;
    documentEpoch.current++;
    setDoc(null);
    setDraft(null);
    setConflict(null);
    setRevisions(null);
    setPreview(null);
    setQuery("");
    setTab("documents");
    setError("");
    setNotice("");
    await loadTeam(id);
  }
  async function openDocument(id: number) {
    if (!detail || busy || !canNavigate()) return;
    const teamId = detail.team.id,
      turn = ++documentEpoch.current;
    setError("");
    try {
      const next = await api<Doc>(`/${teamId}/documents/${id}`);
      if (selected.current !== teamId || turn !== documentEpoch.current) return;
      setDoc(next);
      setDraft(null);
      setConflict(null);
      setRevisions(null);
      setPreview(null);
    } catch (reason) {
      showError(reason);
    }
  }
  async function saveDocument(event: FormEvent) {
    event.preventDefault();
    if (!detail || !draft) return;
    const space = detail.team.id,
      value = draft;
    await action(async () => {
      try {
        const saved = await api<Doc>(
          `/${space}/documents${value.id ? "/" + value.id : ""}`,
          value.id ? "PUT" : "POST",
          value,
        );
        setDoc(saved);
        try { localStorage.removeItem(`wikitide-team-draft:${username}:${space}:${value.id || 'new'}`); } catch { /* Browser storage may be disabled. */ }
        setDraft(null);
        setConflict(null);
        setPreview(null);
        setNotice(ui.t("文档已保存，空间成员可立即阅读。"));
        await refresh();
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 409 && value.id) {
          const latest = await api<Doc>(`/${space}/documents/${value.id}`);
          setConflict(latest);
        }
        throw reason;
      }
    });
  }
  async function memberAction(id: number, kind: string, role?: string) {
    if (!detail) return;
    await action(async () => {
      await api(`/${detail.team.id}/members/${id}`, "POST", {
        action: kind,
        role,
        version: detail.team.version,
      });
      await refresh();
      setNotice(ui.t("成员设置已更新。"));
    });
  }

  return (
    <section className="team-app" aria-label={ui.t("团队空间")}>
      <header className="team-heading">
        <div>
          <p className="eyebrow">WIKITIDE / WORK TOGETHER</p>
          <h1>{ui.t("让知识，在协作中生长")}<span>{ui.t("。")}</span>
          </h1>
          <p className="team-subtitle">{ui.t("你好，")}{username}{ui.t("。在这里，与同伴一起记录、整理和分享。")}</p>
        </div>
        <div className="heading-actions">
          <button onClick={() => openModal("inbox")}>
            <Icon name="bell" />{ui.t("我的邀请")}{inbox.length > 0 && (
              <span className="number-dot">{inbox.length}</span>
            )}
          </button>
          <button className="primary" onClick={() => openModal("create")}>
            <Icon name="plus" />{ui.t("创建空间")}</button>
        </div>
      </header>
      {error && !modal && (
        <div className="team-alert" role="alert">
          {error}
          <button onClick={() => void refresh().catch(showError)}>{ui.t("刷新空间")}</button>
        </div>
      )}
      {notice && (
        <p className="team-notice" role="status">
          {notice}
        </p>
      )}
      <div className="workspace-layout">
        <aside className="spaces-panel" aria-label={ui.t("我的团队")}>
          <div className="section-label">{ui.t("我的空间 ")}<span>{teams.length.toString().padStart(2, "0")}</span>
          </div>
          {teams.map((team) => (
            <button
              key={team.id}
              className={
                "space-button " +
                (detail?.team.id === team.id ? "selected" : "")
              }
              aria-current={detail?.team.id === team.id ? "true" : undefined}
              onClick={() => void switchTeam(team.id)}
              disabled={busy}
            >
              <span className="space-avatar">{team.name.slice(0, 1)}</span>
              <span>
                <strong>{team.name}</strong>
                <small>
                  {roleNames[team.role]} · {team.member_count}{ui.t(" 位成员")}</small>
              </span>
              <span className="space-arrow">↗</span>
            </button>
          ))}
          <button className="add-space" onClick={() => openModal("create")}>
            <Icon name="plus" />{ui.t("开启一个新空间")}</button>
          <div className="space-footnote">
            <Icon name="people" />
            <strong>{ui.t("各有空间，也有共识。")}</strong>
            <p>{ui.t("团队内容仅对成员可见。个人笔记继续在原来的知识库中。")}</p>
            <a href="/browse">{ui.t("返回个人知识库 ↗")}</a>
          </div>
        </aside>
        <div className="workspace-main" aria-busy={loading}>
          {loading ? (
            <div className="workspace-empty" role="status">
              <span className="empty-icon">
                <Icon name="people" />
              </span>
              <h2>{ui.t("正在打开空间…")}</h2>
            </div>
          ) : !detail ? (
            <div className="workspace-empty">
              <span className="empty-icon">
                <Icon name="people" />
              </span>
              <p className="eyebrow">A PLACE FOR YOUR IDEAS</p>
              <h2>{ui.t("从第一个共同的想法开始")}</h2>
              <p>{ui.t("创建团队，邀请已有账户加入，")}<br />{ui.t("为你们的知识留一个共同的位置。")}</p>
              <button className="primary" onClick={() => openModal("create")}>{ui.t("创建第一个空间 ")}<Icon name="arrow" />
              </button>
              <button onClick={() => openModal("inbox")}>{ui.t("查看收到的邀请")}</button>
            </div>
          ) : (
            <>
              <div className="workspace-heading">
                <div>
                  <p className="eyebrow">
                    TEAM SPACE / {String(detail.team.id).padStart(2, "0")}
                  </p>
                  <h2>{detail.team.name}</h2>
                  <p>
                    {detail.team.description ||
                      ui.t("每一次交流，都是知识的新一页。")}
                  </p>
                </div>
                <div className="space-meta">
                  <span className="role-tag">
                    {roleNames[detail.team.role]}
                  </span>
                  <span>
                    {detail.team.member_count}{ui.t(" 位成员 ·")}{" "}
                    {detail.team.document_count}{ui.t(" 篇文档")}</span>
                </div>
              </div>
              <nav className="team-tabs" aria-label={ui.t("空间功能")}>
                {(
                  [
                    ["documents", ui.t("文档")],
                    ["members", ui.t("成员与邀请")],
                    ["activity", ui.t("活动记录")],
                  ] as const
                ).map(([key, title]) => (
                  <button
                    key={key}
                    disabled={busy}
                    aria-current={tab === key ? "page" : undefined}
                    onClick={() => {
                      if (canNavigate()) {
                        setTab(key);
                        setDraft(null);
                        setDoc(null);
                        setConflict(null);
                      }
                    }}
                  >
                    {title}
                  </button>
                ))}
                <button
                  className="refresh-space"
                  disabled={busy}
                  onClick={() => {
                    if (canNavigate()) {
                      documentEpoch.current++;
                      setDoc(null);
                      setDraft(null);
                      setConflict(null);
                      setError("");
                      void refresh().catch(showError);
                    }
                  }}
                >{ui.t("刷新")}</button>
              </nav>
              {tab === "documents" && (
                <>
                  {draft ? (
                    <form className="document-editor" onSubmit={saveDocument}>
                      <div className="editor-title">
                        <h3>{draft.id ? ui.t("编辑文档") : ui.t("新建文档")}</h3>
                        <span>{dirty ? ui.t("有未保存修改") : ui.t("尚未修改")}</span>
                      </div>
                      <label>{ui.t("文档标题")}<input
                          required
                          maxLength={160}
                          value={draft.title}
                          onChange={(e) => {
                            setDraft({ ...draft, title: e.target.value });
                            setPreview(null);
                          }}
                          disabled={busy}
                        />
                      </label>
                      <SharedEditor value={draft.body} disabled={busy} teamId={detail.team.id} csrf={csrf} onChange={body=>{setDraft({...draft,body});setPreview(null);}} />
                      <TeamDraftTools key={`${detail.team.id}:${draft.id || 'new'}`} draft={draft} username={username} teamId={detail.team.id} csrf={csrf} disabled={busy} restore={setDraft} />
                      <p className="muted">{ui.t("支持 Markdown；附件、评论和审核请打开文档协作。")}</p>
                      {preview !== null && (
                        <div
                          className="team-markdown preview-pane"
                          aria-label={ui.t("文档预览")}
                          dangerouslySetInnerHTML={{ __html: preview }}
                        />
                      )}
                      {conflict && (
                        <div className="conflict-panel">
                          <h4>{ui.t("最新版本 v")}{conflict.version}{ui.t(" · 请先对照合并")}</h4>
                          <p>{ui.t("你的输入保留在上方，以下是另一位成员保存的正文。")}</p>
                          <pre>{conflict.body}</pre>
                          <button
                            type="button"
                            onClick={() => {
                              setDraft({ ...draft, version: conflict.version });
                              setConflict(null);
                              setError("");
                            }}
                          >{ui.t("我已对照，基于最新版本继续编辑")}</button>
                        </div>
                      )}
                      <div className="editor-actions">
                        <button
                          type="submit"
                          className="primary"
                          disabled={busy || !!conflict}
                        >
                          {busy ? ui.t("正在保存…") : ui.t("保存文档")}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void action(async () => {
                              const result = await api<{ html: string }>(
                                `/${detail.team.id}/preview`,
                                "POST",
                                { body: draft.body },
                              );
                              setPreview(result.html);
                            })
                          }
                        >{ui.t("预览")}</button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => {
                            if (canNavigate()) {
                              setDraft(null);
                              setConflict(null);
                            }
                          }}
                        >{ui.t("取消")}</button>
                      </div>
                    </form>
                  ) : doc ? (
                    <article className="team-document">
                      <div className="document-toolbar">
                        <button onClick={() => setDoc(null)}>{ui.t("← 文档列表")}</button>
                        <span>
                          v{doc.version} · {date(doc.updated_at)}
                        </span>
                        {writes(detail.team.role) && (
                          <button
                            className="primary"
                            onClick={() => {
                              setDraft({
                                id: doc.id,
                                title: doc.title,
                                body: doc.body,
                                version: doc.version,
                              });
                              setPreview(null);
                            }}
                          >{ui.t("编辑文档")}</button>
                        )}
                        <button
                          onClick={() =>
                            void action(async () =>
                              setRevisions(
                                (
                                  await api<{ revisions: Revision[] }>(
                                    `/${detail.team.id}/documents/${doc.id}/history`,
                                  )
                                ).revisions,
                              ),
                            )
                          }
                        >{ui.t("版本记录")}</button>
                      </div>
                      <h3>{doc.title}</h3>
                      {doc.pdf && <section className="pdf-source" aria-label={ui.t("PDF 原文件")}>
                        <div className="pdf-source-toolbar"><span><strong>{doc.pdf.note_format === 'pdf' ? ui.t("PDF 笔记") : ui.t("来源 PDF")}</strong> · {doc.pdf.filename}</span><div><a href={doc.pdf.url} target="_blank" rel="noopener">{ui.t("打开 PDF")}</a><a href={doc.pdf.download_url}>{ui.t("下载 PDF")}</a><form method="post" action={`/teams/${detail.team.id}/documents/${doc.id}/pdf/convert`}><input type="hidden" name="csrf" value={csrf} /><button type="submit">{ui.t("提取文字 / 转 Markdown")}</button></form></div></div>
                        {doc.pdf.note_format === 'pdf' && <><p className="pdf-source-help">{ui.t("若浏览器无法内嵌阅读，请打开或下载 PDF。")}</p><iframe title={ui.t("PDF 原文阅读")} src={doc.pdf.url} loading="lazy" /></>}
                      </section>}
                      <div
                        className="team-markdown"
                        dangerouslySetInnerHTML={{ __html: doc.html }}
                      />
                      <p><a href={`/teams/${detail.team.id}/documents/${doc.id}/collaborate`}>{ui.t("文档协作 · 附件、段落评论、待办与审核发布 →")}</a></p>
                      {revisions && <HistoryDiff key={`${detail.team.id}-${doc.id}-${doc.version}`} csrf={csrf} teamId={detail.team.id} documentId={doc.id} revisions={revisions} />}
                    </article>
                  ) : (
                    <>
                      <div className="documents-tools">
                        {writes(detail.team.role) && <a href={`/imports?team=${detail.team.id}`}>{ui.t("导入资料 ↗")}</a>}
                        <form
                          role="search"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void loadTeam(detail.team.id, query);
                          }}
                        >
                          <Icon name="search" />
                          <input
                            aria-label={ui.t("搜索团队文档")}
                            placeholder={ui.t("搜索这个空间的文档")}
                            value={query}
                            maxLength={100}
                            onChange={(e) => setQuery(e.target.value)}
                          />
                          <button>{ui.t("搜索")}</button>
                        </form>
                        {writes(detail.team.role) && (
                          <button
                            className="primary"
                            onClick={() => {
                              setDoc(null);
                              setDraft({ title: "", body: "", version: 1 });
                              setPreview(null);
                            }}
                          >
                            <Icon name="plus" />{ui.t("新建文档")}</button>
                        )}
                      </div>
                      {!list?.documents.length ? (
                        <div className="documents-empty">
                          <Icon name="file" />
                          <h3>
                            {query
                              ? ui.t("没有找到匹配的文档")
                              : ui.t("第一篇文档，留给你们")}
                          </h3>
                          <p>
                            {query
                              ? ui.t("试试其他标题或正文关键词。")
                              : writes(detail.team.role)
                                ? ui.t("写一份约定、一次复盘，或一张共同的知识地图。")
                                : ui.t("空间成员发布的文档会出现在这里。")}
                          </p>
                        </div>
                      ) : (
                        <div className="documents-list">
                          <div className="documents-list-head">
                            <span>{ui.t("文档 / ")}{list.total}</span>
                            <span>{ui.t("最近更新")}</span>
                          </div>
                          {list.documents.map((item, index) => (
                            <button
                              className="document-row"
                              key={item.id}
                              onClick={() => void openDocument(item.id)}
                            >
                              <span className="document-index">
                                {String(
                                  (list.page - 1) * list.page_size + index + 1,
                                ).padStart(2, "0")}
                              </span>
                              <span className="document-name">
                                <strong>{item.is_pdf ? 'PDF · ' : ''}{item.title}</strong>
                                <small>{ui.t("版本 ")}{item.version}</small>
                              </span>
                              <time>{date(item.updated_at)}</time>
                              <Icon name="arrow" />
                            </button>
                          ))}
                        </div>
                      )}
                      {list && list.total > list.page_size && (
                        <div className="pagination">
                          <button
                            disabled={list.page === 1}
                            onClick={() =>
                              void loadTeam(
                                detail.team.id,
                                query,
                                list.page - 1,
                              )
                            }
                          >{ui.t("上一页")}</button>
                          <span>{ui.t("第 ")}{list.page}{ui.t(" 页 / 共")}{" "}
                            {Math.ceil(list.total / list.page_size)}{ui.t(" 页")}</span>
                          <button
                            disabled={list.page * list.page_size >= list.total}
                            onClick={() =>
                              void loadTeam(
                                detail.team.id,
                                query,
                                list.page + 1,
                              )
                            }
                          >{ui.t("下一页")}</button>
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
              {tab === "members" && (
                <div className="team-members">
                  <div className="section-toolbar">
                    <div>
                      <h3>{ui.t("一起记录的人")}</h3>
                      <p className="muted">{ui.t("读者可阅读，编辑者可写文档，管理员可管理普通成员。")}</p>
                    </div>
                    {manages(detail.team.role) && (
                      <button
                        className="primary"
                        onClick={() => openModal("invite")}
                      >
                        <Icon name="plus" />{ui.t("邀请成员")}</button>
                    )}
                  </div>
                  <ul>
                    {detail.members.map((member) => (
                      <MemberRow
                        key={member.user_id}
                        member={member}
                        detail={detail}
                        busy={busy}
                        act={memberAction}
                      />
                    ))}
                  </ul>
                  {manages(detail.team.role) &&
                    detail.invitations.length > 0 && (
                      <div className="invitation-log">
                        <h4>{ui.t("邀请记录（最近 50 条）")}</h4>
                        {detail.invitations.map((invite) => (
                          <div key={invite.id}>
                            <span>
                              {invite.username} · {roleNames[invite.role]}
                            </span>
                            <span className="muted">
                              {
                                {
                                  pending: ui.t("待接受"),
                                  accepted: ui.t("已加入"),
                                  declined: ui.t("已拒绝"),
                                  expired: ui.t("已过期"),
                                  revoked: ui.t("已撤销"),
                                }[invite.status]
                              }
                            </span>
                            {invite.status === "pending" &&
                              (detail.team.role === "owner" ||
                                invite.role !== "admin") && (
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void action(async () => {
                                      await api(
                                        `/${detail.team.id}/invitations/${invite.id}/revoke`,
                                        "POST",
                                        { version: detail.team.version },
                                      );
                                      await refresh();
                                    })
                                  }
                                >{ui.t("撤销")}</button>
                              )}
                          </div>
                        ))}
                      </div>
                    )}
                  <div className="membership-footer">
                    {detail.team.role === "owner" ? (
                      <button onClick={() => openModal("transfer")}>{ui.t("转让所有权")}</button>
                    ) : (
                      <button
                        className="text-danger"
                        disabled={busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              ui.t("退出后需要新的邀请才能重新加入，确定退出吗？"),
                            )
                          )
                            void action(async () => {
                              await api(`/${detail.team.id}/leave`, "POST", {
                                version: detail.team.version,
                              });
                              selected.current = null;
                              setDetail(null);
                              setDoc(null);
                              setDraft(null);
                              const spaces = await loadOverview();
                              if (spaces.length) await loadTeam(spaces[0].id);
                              setNotice(ui.t("已退出空间。"));
                            });
                        }}
                      >{ui.t("退出空间")}</button>
                    )}
                    <p className="muted">{ui.t("所有者需要先转让所有权，才能退出。")}</p>
                  </div>
                </div>
              )}
              {tab === "activity" && (
                <div className="team-activity">
                  <h3>{ui.t("空间里的每一步")}</h3>
                  <p className="muted">{ui.t("最近 20 条活动，记录成员与文档变化。")}</p>
                  {events.map((item) => (
                    <div className="activity-row" key={item.id}>
                      <span className="activity-dot" />
                      <div>
                        <strong>{ui.t(item.detail)}</strong>
                        <small>
                          {item.actor} · {date(item.created_at)}
                        </small>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {modal && (
        <Modal
          title={
            {
              create: ui.t("创建团队空间"),
              invite: ui.t("邀请新成员"),
              inbox: ui.t("收到的邀请"),
              transfer: ui.t("转让空间所有权"),
            }[modal]
          }
          close={closeModal}
        >
          {error && (
            <p className="team-alert" role="alert">
              {error}
            </p>
          )}
          {modal === "create" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  const created = await api<{ id: number }>("", "POST", {
                    name,
                    description,
                  });
                  setName("");
                  setDescription("");
                  setModal(null);
                  await loadOverview();
                  await switchTeam(created.id);
                  setNotice(ui.t("空间已创建，邀请同伴一起开始吧。"));
                });
              }}
            >
              <p className="muted">{ui.t("成员接受邀请后才能进入。你将成为空间所有者。")}</p>
              <label>{ui.t("空间名称")}<input
                  autoFocus
                  required
                  maxLength={80}
                  placeholder={ui.t("例如：产品研发手记")}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label>{ui.t("空间简介")}<textarea
                  maxLength={500}
                  rows={3}
                  placeholder={ui.t("这里会留下什么？")}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={busy}
                />
              </label>
              <button className="primary" disabled={busy}>
                {busy ? ui.t("正在创建…") : ui.t("创建空间")}
              </button>
            </form>
          )}
          {modal === "invite" && detail && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  await api(`/${detail.team.id}/invitations`, "POST", {
                    username: invitee,
                    role: inviteRole,
                    version: detail.team.version,
                  });
                  setInvitee("");
                  setModal(null);
                  await refresh();
                  setNotice(ui.t("邀请已发送到对方的站内邀请箱，有效期 7 天。"));
                });
              }}
            >
              <p className="muted">{ui.t("邀请已有 WikiTide 账户，对方接受后加入。不会发送邮件。")}</p>
              <label>{ui.t("账户名")}<input
                  autoFocus
                  required
                  maxLength={64}
                  value={invitee}
                  onChange={(e) => setInvitee(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label>{ui.t("空间角色")}<select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  disabled={busy}
                >
                  {detail.team.role === "owner" && (
                    <option value="admin">{ui.t("管理员")}</option>
                  )}
                  <option value="editor">{ui.t("编辑者")}</option>
                  <option value="reader">{ui.t("读者")}</option>
                </select>
              </label>
              <button className="primary" disabled={busy}>{ui.t("发送邀请")}</button>
            </form>
          )}
          {modal === "inbox" && (
            <div className="inbox-list">
              {inbox.length === 0 ? (
                <p className="muted">{ui.t("暂时没有待处理邀请。")}</p>
              ) : (
                inbox.map((invite) => (
                  <div key={invite.id}>
                    <h3>{invite.team_name}</h3>
                    <p>
                      {invite.invited_by}{ui.t(" 邀请你以")}{roleNames[invite.role]}{ui.t("身份加入")}</p>
                    <small>{ui.t("有效期至 ")}{date(invite.expires_at)}</small>
                    <div className="inline-actions">
                      {(["accept", "decline"] as const).map((kind) => (
                        <button
                          className={kind === "accept" ? "primary" : ""}
                          key={kind}
                          disabled={busy}
                          onClick={() =>
                            void action(async () => {
                              await api(
                                `/invitations/${invite.id}/respond`,
                                "POST",
                                { action: kind },
                              );
                              await loadOverview();
                              setNotice(
                                kind === "accept"
                                  ? ui.t("已加入空间，可从左侧打开。")
                                  : ui.t("已拒绝邀请。"),
                              );
                            })
                          }
                        >
                          {kind === "accept" ? ui.t("接受邀请") : ui.t("拒绝")}
                        </button>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
          {modal === "transfer" && detail && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  await api(`/${detail.team.id}/transfer`, "POST", {
                    user_id: Number(transferTo),
                    version: detail.team.version,
                  });
                  setModal(null);
                  await refresh();
                  setNotice(ui.t("所有权已转让，你现在是空间管理员。"));
                });
              }}
            >
              <p className="team-warning">{ui.t("转让后，对方将拥有最高管理权限，你会成为管理员。请选择你信任的现有成员。")}</p>
              <label>{ui.t("新的所有者")}<select
                  required
                  value={transferTo}
                  onChange={(e) => setTransferTo(e.target.value)}
                  disabled={busy}
                >
                  <option value="">{ui.t("请选择成员")}</option>
                  {detail.members
                    .filter(
                      (member) =>
                        member.user_id !== detail.user_id && member.active,
                    )
                    .map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.username}
                      </option>
                    ))}
                </select>
              </label>
              <button className="primary" disabled={busy || !transferTo}>{ui.t("确认转让")}</button>
            </form>
          )}
        </Modal>
      )}
    </section>
  );
}
