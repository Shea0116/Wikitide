import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../models.dart';
import 'agent_chat_page.dart';
import 'agent_session.dart';
import 'local_memory_store.dart';
import 'memory_search.dart';

class NativeMemoryPage extends StatefulWidget {
  const NativeMemoryPage({super.key, required this.origin});
  final String origin;
  @override
  State<NativeMemoryPage> createState() => _NativeMemoryPageState();
}

class _NativeMemoryPageState extends State<NativeMemoryPage>
    with WidgetsBindingObserver {
  late final AgentSession session = AgentSession(widget.origin);
  final username = TextEditingController();
  final password = TextEditingController();
  final search = TextEditingController();
  List<LocalRecord> memories = [], projects = [];
  final selected = <String>{};
  String? projectId;
  String filter = 'all';
  String? error;
  bool busy = true;
  int tab = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    session.addListener(changed);
    run(session.initialize);
  }

  void changed() {
    if (!mounted) return;
    setState(() {
      if (!session.unlocked) {
        memories = [];
        projects = [];
        selected.clear();
        search.clear();
        projectId = null;
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      session.lock();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    session.removeListener(changed);
    session.dispose();
    username.dispose();
    password.dispose();
    search.dispose();
    NativeAgentReadiness.setBlocked(this, false);
    super.dispose();
  }

  Future<void> run(Future<void> Function() action) async {
    if (mounted) {
      setState(() {
        busy = true;
        error = null;
      });
    }
    try {
      await action();
    } catch (failure) {
      if (mounted) setState(() => error = failure.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> reload() async {
    final store = session.store;
    if (store == null) return;
    final nextMemories = await store.list(LocalRecordKind.memory);
    final nextProjects = await store.list(LocalRecordKind.project);
    if (!mounted || !session.unlocked || !identical(store, session.store)) {
      return;
    }
    setState(() {
      memories = nextMemories;
      projects = nextProjects;
      selected.removeWhere((id) => !memories.any((row) => row.id == id));
    });
  }

  Future<void> unlock(Account account) => run(() async {
    await session.unlock(account, allowCreate: true);
    await reload();
  });

  Future<bool> confirm(String title, String message, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(message),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(action),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> edit(LocalRecordKind kind, [LocalRecord? row]) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => LocalRecordEditor(
          session: session,
          kind: kind,
          record: row,
          projects: projects,
          initialProjectId: projectId,
        ),
      ),
    );
    if (session.unlocked) await run(reload);
  }

  Future<void> remove(LocalRecordKind kind, LocalRecord row) async {
    final approved = await confirm(
      '删除本机内容',
      kind == LocalRecordKind.memory
          ? '删除后无法撤销。为避免已遗忘内容出现在旧回答中，本机保存的对话与摘要也会一并清除。'
          : '将删除项目及其本机记忆、对话和摘要。此操作无法撤销。',
      '删除',
    );
    if (!approved || !session.unlocked) return;
    await run(() async {
      await session.store!.remove(kind, row);
      if (projectId == row.id) projectId = null;
      await reload();
    });
  }

  Future<void> export(bool markdown) async {
    final rows = memories.where((row) => selected.contains(row.id)).toList();
    if (rows.isEmpty) return;
    final approved = await confirm(
      '导出明文副本',
      '将导出 ${rows.length} 条记忆及相关项目资料。导出的 JSON/Markdown 不再受本机加密保护，请妥善保存；设备密钥不会包含在内。Markdown 供阅读，恢复导入请使用 JSON。',
      '导出明文',
    );
    if (!approved || !session.unlocked) return;
    final linked = projects
        .where((row) => rows.any((memory) => memory.projectId == row.id))
        .toList();
    final text = markdown
        ? MemoryTransfer.markdown(rows, linked)
        : const JsonEncoder.withIndent('  ').convert(
            MemoryTransfer.exportBundle(
              origin: session.api.server,
              accountId: session.account!.id,
              memories: rows,
              projects: linked,
            ),
          );
    await run(() async {
      final saved = await const MethodChannel('wikitide/attachments')
          .invokeMethod<bool>('save', {
            'bytes': utf8.encode(text),
            'name': 'wikitide-memory.${markdown ? 'md' : 'json'}',
          });
      if (mounted && saved == true) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('明文副本已导出')));
      }
    });
  }

  Future<void> import() async {
    final text = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => MemoryImportPage(session: session)),
    );
    if (text == null || !session.unlocked) return;
    await run(() async {
      if (utf8.encode(text).length > MemoryTransfer.maxBytes) {
        throw const LocalMemoryException('导入文件超过 8 MiB');
      }
      final decoded = jsonDecode(text);
      if (decoded is! Map) throw const LocalMemoryException('导入 JSON 格式无效');
      final bundle = Map<String, dynamic>.from(decoded);
      final preview = MemoryTransfer.preview(
        bundle,
        existingProjects: projects,
        existingMemories: memories,
      );
      if (!mounted) return;
      final approved = await confirm(
        '导入预览',
        '来源站点：${bundle['origin']}\n来源账户：${bundle['account_id']}\n新增项目 ${preview.projects.length} 个，新增记忆 ${preview.memories.length} 条。\n${preview.conflicts.length} 条 ID 冲突将跳过，不会覆盖。\n导入内容会使用当前本机库重新加密；原导出 revision 保留在导入来源中。',
        '只导入新增项',
      );
      if (!approved || !session.unlocked) return;
      await session.store!.importNew(preview.projects, preview.memories);
      await reload();
    });
  }

  Widget loginPane() => Center(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 600),
      child: ListView(
        shrinkWrap: true,
        padding: const EdgeInsets.all(24),
        children: [
          const Text(
            '本机记忆库',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 12),
          Text(
            '当前站点：${session.api.server}\n原生账户：${session.account?.username ?? '尚未登录'}',
          ),
          const SizedBox(height: 12),
          const Text(
            '本机库使用设备钥匙串中的密钥加密。离线时只能显式解锁已绑定账户；云端生成需要联网并验证原生身份。首次使用需独立登录，与网页 Cookie 分开。\n\n设备密钥丢失且没有导出副本时无法恢复。本版本不离线保存受权限控制的团队资料。',
          ),
          if (session.message != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(session.message!),
            ),
          const SizedBox(height: 18),
          for (final account in session.boundAccounts)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: OutlinedButton.icon(
                onPressed: busy ? null : () => unlock(account),
                icon: const Icon(Icons.lock_open),
                label: Text('解锁 ${account.username}（#${account.id}）'),
              ),
            ),
          const Divider(height: 32),
          TextField(
            controller: username,
            decoration: const InputDecoration(labelText: '原生登录用户名'),
            autofillHints: const [AutofillHints.username],
          ),
          TextField(
            controller: password,
            obscureText: true,
            decoration: const InputDecoration(labelText: '密码'),
            autofillHints: const [AutofillHints.password],
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: busy
                ? null
                : () => run(() async {
                    final secret = password.text;
                    password.clear();
                    await session.login(username.text, secret);
                  }),
            child: const Text('联网登录 / 绑定账户'),
          ),
          if (session.account != null && session.verified)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: FilledButton.tonal(
                onPressed: busy ? null : () => unlock(session.account!),
                child: const Text('创建或解锁当前账户本机库'),
              ),
            ),
        ],
      ),
    ),
  );

  Widget libraryPane() {
    final rows = tab == 0
        ? memories.where((row) {
            final text = '${row.payload['title']} ${row.payload['content']}'
                .toLowerCase();
            return (projectId == null || row.projectId == projectId) &&
                (filter == 'all' || row.status == filter) &&
                text.contains(search.text.toLowerCase());
          }).toList()
        : projects;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            '站点 ${session.api.server} · 原生账户 ${session.account!.username}（#${session.account!.id}）\n本机保存 · 仅选择后发送到云端 · 不与网页共享本机正文',
            textAlign: TextAlign.center,
          ),
        ),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          alignment: WrapAlignment.center,
          children: [
            SegmentedButton<int>(
              segments: const [
                ButtonSegment(value: 0, label: Text('记忆')),
                ButtonSegment(value: 1, label: Text('项目')),
              ],
              selected: {tab},
              onSelectionChanged: (value) => setState(() => tab = value.first),
            ),
            FilledButton.icon(
              onPressed: busy
                  ? null
                  : () => edit(
                      tab == 0
                          ? LocalRecordKind.memory
                          : LocalRecordKind.project,
                    ),
              icon: const Icon(Icons.add),
              label: Text(tab == 0 ? '新建记忆' : '新建项目'),
            ),
            OutlinedButton.icon(
              onPressed: busy
                  ? null
                  : () async {
                      await Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => AgentChatPage(
                            session: session,
                            projectId: projectId,
                          ),
                        ),
                      );
                      if (session.unlocked) await run(reload);
                    },
              icon: const Icon(Icons.chat_outlined),
              label: const Text('本机问答'),
            ),
            TextButton(
              onPressed: busy ? null : import,
              child: const Text('导入 JSON'),
            ),
            PopupMenuButton<bool>(
              enabled: selected.isNotEmpty && !busy,
              tooltip: '导出选中记忆',
              onSelected: export,
              itemBuilder: (_) => const [
                PopupMenuItem(value: false, child: Text('导出明文 JSON')),
                PopupMenuItem(value: true, child: Text('导出明文 Markdown')),
              ],
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Text('导出已选 ${selected.length} 条'),
              ),
            ),
          ],
        ),
        if (tab == 0)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Wrap(
              spacing: 16,
              runSpacing: 8,
              children: [
                SizedBox(
                  width: 260,
                  child: TextField(
                    controller: search,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(
                      labelText: '本机关键词搜索',
                      prefixIcon: Icon(Icons.search),
                    ),
                  ),
                ),
                DropdownButton<String?>(
                  value: projectId,
                  items: [
                    const DropdownMenuItem(value: null, child: Text('全部范围')),
                    ...projects.map(
                      (row) => DropdownMenuItem(
                        value: row.id,
                        child: Text(row.payload['title'] as String),
                      ),
                    ),
                  ],
                  onChanged: (value) => setState(() => projectId = value),
                ),
                DropdownButton<String>(
                  value: filter,
                  items: const [
                    DropdownMenuItem(value: 'all', child: Text('全部状态')),
                    DropdownMenuItem(value: 'active', child: Text('启用')),
                    DropdownMenuItem(value: 'disabled', child: Text('停用')),
                    DropdownMenuItem(value: 'archived', child: Text('归档')),
                  ],
                  onChanged: (value) => setState(() => filter = value!),
                ),
              ],
            ),
          ),
        Expanded(
          child: rows.isEmpty
              ? const Center(child: Text('暂无内容。可以离线创建、编辑和导入。'))
              : ListView.builder(
                  itemCount: rows.length,
                  itemBuilder: (context, index) {
                    final row = rows[index];
                    final expired =
                        row.payload['expires_at'] is int &&
                        (row.payload['expires_at'] as int) <=
                            DateTime.now().millisecondsSinceEpoch;
                    return ListTile(
                      leading: tab == 0
                          ? Checkbox(
                              value: selected.contains(row.id),
                              onChanged: (value) => setState(() {
                                if (value == true) {
                                  selected.add(row.id);
                                } else {
                                  selected.remove(row.id);
                                }
                              }),
                            )
                          : const Icon(Icons.folder_outlined),
                      title: Text(row.payload['title'] as String? ?? '记忆'),
                      subtitle: Text(
                        '${row.status} · revision ${row.revision}${expired ? ' · 已过期' : ''}\n${row.payload['content'] ?? row.payload['goal'] ?? ''}',
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                      onTap: busy
                          ? null
                          : () => edit(
                              tab == 0
                                  ? LocalRecordKind.memory
                                  : LocalRecordKind.project,
                              row,
                            ),
                      trailing: IconButton(
                        tooltip: '删除',
                        onPressed: busy
                            ? null
                            : () => remove(
                                tab == 0
                                    ? LocalRecordKind.memory
                                    : LocalRecordKind.project,
                                row,
                              ),
                        icon: const Icon(Icons.delete_outline),
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('本机记忆'),
      actions: [
        if (session.unlocked)
          IconButton(
            tooltip: '锁定本机库',
            onPressed: busy ? null : session.lock,
            icon: const Icon(Icons.lock_outline),
          ),
        if (session.api.token != null)
          IconButton(
            tooltip: '退出原生账户',
            onPressed: busy ? null : () => run(session.logout),
            icon: const Icon(Icons.logout),
          ),
        if (session.unlocked)
          IconButton(
            tooltip: '删除本机库和设备密钥',
            onPressed: busy
                ? null
                : () async {
                    if (await confirm(
                      '删除整个本机库',
                      '这会删除当前站点与账户的全部本机记忆、项目、对话、摘要及设备密钥。需要导出时请先取消。此操作无法撤销。',
                      '删除全部',
                    )) {
                      await run(session.deleteLibrary);
                    }
                  },
            icon: const Icon(Icons.delete_forever_outlined),
          ),
      ],
    ),
    body: Column(
      children: [
        if (busy) const LinearProgressIndicator(),
        if (error != null)
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        Expanded(child: session.unlocked ? libraryPane() : loginPane()),
      ],
    ),
  );
}

class LocalRecordEditor extends StatefulWidget {
  const LocalRecordEditor({
    super.key,
    required this.session,
    required this.kind,
    required this.projects,
    this.record,
    this.initialProjectId,
  });
  final AgentSession session;
  final LocalRecordKind kind;
  final List<LocalRecord> projects;
  final LocalRecord? record;
  final String? initialProjectId;
  @override
  State<LocalRecordEditor> createState() => _LocalRecordEditorState();
}

class _LocalRecordEditorState extends State<LocalRecordEditor> {
  late final title = TextEditingController(
    text: widget.record?.payload['title'] as String? ?? '',
  );
  late final content = TextEditingController(
    text:
        (widget.record?.payload[isMemory ? 'content' : 'goal'] as String?) ??
        '',
  );
  late final source = TextEditingController(
    text: widget.record?.payload['source']?['description'] as String? ?? '手动输入',
  );
  late String status = widget.record?.status ?? 'active';
  late String kind = widget.record?.payload['kind'] as String? ?? 'preference';
  late String? projectId = widget.record?.projectId ?? widget.initialProjectId;
  DateTime? expiry;
  bool dirty = false, saving = false, leaving = false;
  String? error;
  bool get isMemory => widget.kind == LocalRecordKind.memory;

  @override
  void initState() {
    super.initState();
    final raw = widget.record?.payload['expires_at'];
    if (raw is int) expiry = DateTime.fromMillisecondsSinceEpoch(raw);
    for (final controller in [title, content, source]) {
      controller.addListener(markDirty);
    }
    widget.session.addListener(onSession);
  }

  void markDirty() {
    if (mounted) setState(() => dirty = true);
    NativeAgentReadiness.setBlocked(this, true);
  }

  void onSession() {
    if (!widget.session.unlocked && mounted) {
      title.clear();
      content.clear();
      source.clear();
      leaving = true;
      NativeAgentReadiness.setBlocked(this, false);
      Navigator.of(context).pop();
    }
  }

  @override
  void dispose() {
    widget.session.removeListener(onSession);
    for (final controller in [title, content, source]) {
      controller.removeListener(markDirty);
      controller.dispose();
    }
    NativeAgentReadiness.setBlocked(this, false);
    super.dispose();
  }

  Future<void> leave() async {
    if (saving) return;
    final allowed =
        !dirty ||
        (await showDialog<bool>(
              context: context,
              builder: (context) => AlertDialog(
                title: const Text('放弃未保存修改？'),
                content: const Text('当前修改尚未写入本机加密库。'),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('继续编辑'),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('放弃修改'),
                  ),
                ],
              ),
            )) ==
            true;
    if (allowed && mounted) {
      setState(() => leaving = true);
      Navigator.of(context).pop();
    }
  }

  Future<void> save() async {
    setState(() {
      saving = true;
      error = null;
    });
    NativeAgentReadiness.setBlocked(this, true);
    try {
      final payload = <String, dynamic>{
        ...?widget.record?.payload,
        'title': title.text.trim(),
        if (isMemory) ...{
          'content': content.text.trim(),
          'kind': kind,
          'expires_at': expiry?.millisecondsSinceEpoch,
          'source': {'kind': 'manual', 'description': source.text.trim()},
        } else
          'goal': content.text.trim(),
      };
      await widget.session.store!.save(
        widget.kind,
        payload,
        id: widget.record?.id,
        expectedRevision: widget.record?.revision ?? 0,
        status: status,
        projectId: isMemory ? projectId : null,
      );
      if (mounted) {
        setState(() {
          dirty = false;
          leaving = true;
        });
        Navigator.of(context).pop();
      }
    } catch (failure) {
      if (mounted) setState(() => error = failure.toString());
    } finally {
      if (mounted) setState(() => saving = false);
      NativeAgentReadiness.setBlocked(this, dirty);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: leaving || (!dirty && !saving),
    onPopInvokedWithResult: (didPop, _) {
      if (!didPop) leave();
    },
    child: Scaffold(
      appBar: AppBar(
        title: Text(isMemory ? '编辑本机记忆' : '编辑本机项目'),
        actions: [
          TextButton(
            onPressed: saving ? null : save,
            child: const Text('加密保存'),
          ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(
                '仅保存于 ${widget.session.api.server} / ${widget.session.account!.username} 的本机库',
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
              if (saving) const LinearProgressIndicator(),
              TextField(
                controller: title,
                maxLength: 200,
                decoration: const InputDecoration(labelText: '标题'),
              ),
              TextField(
                controller: content,
                minLines: 8,
                maxLines: 20,
                maxLength: isMemory ? 4000 : 20000,
                decoration: InputDecoration(
                  labelText: isMemory ? '记忆正文' : '项目目标与说明',
                ),
              ),
              if (isMemory) ...[
                DropdownButtonFormField<String>(
                  initialValue: kind,
                  decoration: const InputDecoration(labelText: '类型'),
                  items: const [
                    DropdownMenuItem(value: 'preference', child: Text('偏好')),
                    DropdownMenuItem(
                      value: 'fact',
                      child: Text('事实（云端回答仍需核实来源）'),
                    ),
                    DropdownMenuItem(value: 'decision', child: Text('决定')),
                    DropdownMenuItem(value: 'progress', child: Text('进度')),
                  ],
                  onChanged: (value) {
                    setState(() => kind = value!);
                    markDirty();
                  },
                ),
                DropdownButtonFormField<String?>(
                  initialValue: projectId,
                  decoration: const InputDecoration(labelText: '作用范围'),
                  items: [
                    const DropdownMenuItem(value: null, child: Text('当前账户')),
                    ...widget.projects
                        .where(
                          (row) =>
                              row.status == 'active' || row.id == projectId,
                        )
                        .map(
                          (row) => DropdownMenuItem(
                            value: row.id,
                            child: Text(row.payload['title'] as String),
                          ),
                        ),
                  ],
                  onChanged: (value) {
                    setState(() => projectId = value);
                    markDirty();
                  },
                ),
                TextField(
                  controller: source,
                  maxLength: 1000,
                  decoration: const InputDecoration(
                    labelText: '来源说明（不导入受权限控制的团队摘录）',
                  ),
                ),
                Wrap(
                  spacing: 12,
                  children: [
                    TextButton(
                      onPressed: () async {
                        final value = await showDatePicker(
                          context: context,
                          firstDate: DateTime(2020),
                          lastDate: DateTime(2100),
                          initialDate:
                              expiry ??
                              DateTime.now().add(const Duration(days: 30)),
                        );
                        if (value != null && mounted) {
                          setState(() => expiry = value);
                          markDirty();
                        }
                      },
                      child: Text(
                        expiry == null
                            ? '设置有效期'
                            : '有效期：${expiry!.toLocal().toString().substring(0, 10)}',
                      ),
                    ),
                    if (expiry != null)
                      TextButton(
                        onPressed: () {
                          setState(() => expiry = null);
                          markDirty();
                        },
                        child: const Text('取消有效期'),
                      ),
                  ],
                ),
              ],
              DropdownButtonFormField<String>(
                initialValue: status,
                decoration: const InputDecoration(labelText: '状态'),
                items: const [
                  DropdownMenuItem(value: 'active', child: Text('启用')),
                  DropdownMenuItem(value: 'disabled', child: Text('停用')),
                  DropdownMenuItem(value: 'archived', child: Text('归档')),
                ],
                onChanged: (value) {
                  setState(() => status = value!);
                  markDirty();
                },
              ),
              const SizedBox(height: 24),
              const Text('修改记忆会清除依赖的本机对话与摘要，防止旧内容继续进入上下文。'),
            ],
          ),
        ),
      ),
    ),
  );
}

class MemoryImportPage extends StatefulWidget {
  const MemoryImportPage({super.key, required this.session});
  final AgentSession session;
  @override
  State<MemoryImportPage> createState() => _MemoryImportPageState();
}

class _MemoryImportPageState extends State<MemoryImportPage> {
  final text = TextEditingController();
  String? error;
  bool leaving = false;
  @override
  void initState() {
    super.initState();
    widget.session.addListener(onSession);
    text.addListener(onChange);
  }

  void onChange() {
    NativeAgentReadiness.setBlocked(this, text.text.isNotEmpty);
  }

  void onSession() {
    if (!widget.session.unlocked && mounted) {
      text.clear();
      leaving = true;
      Navigator.pop(context);
    }
  }

  @override
  void dispose() {
    widget.session.removeListener(onSession);
    text.dispose();
    NativeAgentReadiness.setBlocked(this, false);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('导入本机记忆 JSON')),
    body: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          const Text('粘贴或选择明文 JSON，下一步会显示新增和冲突预览。不会自动覆盖已有记忆。Markdown 导出只供阅读。'),
          if (Platform.isMacOS)
            TextButton.icon(
              onPressed: () async {
                try {
                  final raw = await const MethodChannel(
                    'wikitide/attachments',
                  ).invokeMethod<String>('readMemoryImport');
                  if (raw != null && mounted) text.text = raw;
                } catch (_) {
                  if (mounted) {
                    setState(() => error = '文件读取失败，请粘贴 JSON 或选择 8 MiB 以内的文件');
                  }
                }
              },
              icon: const Icon(Icons.file_open_outlined),
              label: const Text('选择 JSON 文件'),
            ),
          if (error != null) Text(error!),
          Expanded(
            child: TextField(
              controller: text,
              expands: true,
              minLines: null,
              maxLines: null,
              decoration: const InputDecoration(
                labelText: 'JSON 内容',
                alignLabelWithHint: true,
              ),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: () {
              if (utf8.encode(text.text).length > MemoryTransfer.maxBytes) {
                setState(() => error = '导入内容超过 8 MiB');
                return;
              }
              leaving = true;
              Navigator.pop(context, text.text);
            },
            child: const Text('检查并预览导入'),
          ),
        ],
      ),
    ),
  );
}
