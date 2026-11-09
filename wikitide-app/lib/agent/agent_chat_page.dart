import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import '../models.dart';
import 'agent_session.dart';
import 'local_memory_store.dart';
import 'memory_search.dart';

class AgentChatPage extends StatefulWidget {
  const AgentChatPage({super.key, required this.session, this.projectId});
  final AgentSession session;
  final String? projectId;
  @override
  State<AgentChatPage> createState() => _AgentChatPageState();
}

class _AgentChatPageState extends State<AgentChatPage> {
  final question = TextEditingController();
  final noteId = TextEditingController();
  List<LocalRecord> memories = [];
  List<LocalRecord> candidates = [];
  final selected = <String>{};
  List<Json> history = [];
  Json summary = {'schema_version': 1, 'goal': ''};
  LocalRecord? conversation;
  String answer = '', status = '', error = '';
  List<dynamic> sources = [];
  bool busy = true, generating = false, webSearch = false, leaving = false;
  int requestId = 0;
  AgentSession get session => widget.session;

  @override
  void initState() {
    super.initState();
    session.addListener(onSession);
    question.addListener(onQuestion);
    load();
  }

  void onQuestion() {
    NativeAgentReadiness.setBlocked(
      this,
      question.text.isNotEmpty || generating,
    );
    if (!generating && mounted) {
      setState(() {
        candidates = searchMemories(
          memories,
          question.text,
          projectId: widget.projectId,
        );
      });
    }
  }

  void onSession() {
    if (!session.unlocked && mounted) {
      requestId++;
      question.clear();
      noteId.clear();
      history.clear();
      summary.clear();
      memories.clear();
      candidates.clear();
      selected.clear();
      sources.clear();
      answer = '';
      leaving = true;
      NativeAgentReadiness.setBlocked(this, false);
      Navigator.of(context).pop();
    }
  }

  @override
  void dispose() {
    requestId++;
    session.transient.cancel();
    session.removeListener(onSession);
    question.dispose();
    noteId.dispose();
    NativeAgentReadiness.setBlocked(this, false);
    super.dispose();
  }

  Future<void> load() async {
    try {
      final store = session.store!;
      if (widget.projectId != null) {
        final projects = await store.list(LocalRecordKind.project);
        if (!projects.any(
          (row) => row.id == widget.projectId && row.status == 'active',
        )) {
          throw const LocalMemoryException('该项目已归档或停用，请返回选择启用的项目');
        }
      }
      final rows = await store.list(LocalRecordKind.memory);
      final sessions = await store.list(LocalRecordKind.conversation);
      final previous = sessions
          .where((row) => row.projectId == widget.projectId)
          .firstOrNull;
      if (!mounted || !session.unlocked) return;
      setState(() {
        memories = rows;
        candidates = searchMemories(rows, '', projectId: widget.projectId);
        conversation = previous;
        if (previous != null) {
          history = (previous.payload['history'] as List? ?? [])
              .map((row) => Map<String, dynamic>.from(row as Map))
              .toList();
          summary = Map<String, dynamic>.from(
            previous.payload['summary'] as Map? ??
                {'schema_version': 1, 'goal': ''},
          );
          sources = previous.payload['sources'] as List? ?? [];
        }
      });
    } catch (failure) {
      if (mounted) setState(() => error = failure.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  List<LocalRecord> get chosen => memories
      .where(
        (row) =>
            selected.contains(row.id) &&
            memoryIsUsable(row, projectId: widget.projectId),
      )
      .toList();
  Json body(String prompt, {List<Json>? messages, Json? compressed}) => {
    'question': prompt,
    'note_id': noteId.text.trim().isEmpty
        ? null
        : int.tryParse(noteId.text.trim()),
    'web_search': webSearch,
    'history': (messages ?? history)
        .skip(
          (messages ?? history).length > 12
              ? (messages ?? history).length - 12
              : 0,
        )
        .toList(),
    'summary': compressed ?? summary,
    'memory_context': chosen
        .map(
          (row) => {
            'local_id': row.id,
            'revision': row.revision,
            'content': row.payload['content'],
          },
        )
        .toList(),
  };

  Future<bool> review({required bool summarizeOnly}) async {
    final prompt = question.text.trim();
    if (!summarizeOnly && (prompt.isEmpty || prompt.length > 1000)) {
      setState(() => error = '问题需要 1～1000 个字符');
      return false;
    }
    if (noteId.text.trim().isNotEmpty &&
        (int.tryParse(noteId.text.trim()) ?? 0) <= 0) {
      setState(() => error = '公开笔记 ID 需要正整数');
      return false;
    }
    return await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: Text(summarizeOnly ? '检查摘要请求' : '检查待发送内容'),
            content: SizedBox(
              width: 640,
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '发送到 ${session.api.server}\n原生账户：${session.account!.username}\n\n本机保存、按需发送到 WikiTide 与生成服务。WikiTide 不保存该次聊天正文；生成服务按其自身保留规则处理。需要联网，本机没有离线生成模型。',
                    ),
                    const Divider(height: 24),
                    if (!summarizeOnly) SelectableText('本轮问题：\n$prompt\n'),
                    Text(
                      '公开笔记：${noteId.text.isEmpty ? '未指定' : noteId.text} · 联网搜索：${webSearch ? '开启' : '关闭'}',
                    ),
                    Text(
                      '最近历史 ${history.length} 条；${history.length >= 12 && !summarizeOnly ? '本轮会先调用一次摘要，之后继续问答。' : '摘要请求会产生模型调用。'}',
                    ),
                    ExpansionTile(
                      title: const Text('查看待发送历史'),
                      children: history
                          .map(
                            (row) => Padding(
                              padding: const EdgeInsets.all(8),
                              child: SelectableText(
                                '${row['role']}：${row['content']}',
                              ),
                            ),
                          )
                          .toList(),
                    ),
                    ExpansionTile(
                      title: const Text('查看当前摘要'),
                      children: [
                        SelectableText(
                          const JsonEncoder.withIndent('  ').convert(summary),
                        ),
                      ],
                    ),
                    Text('本轮选择 ${chosen.length} 条记忆（最多 5 条）：'),
                    for (final row in chosen)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: SelectableText(
                          '${row.payload['title']} · revision ${row.revision}\n${row.payload['content']}',
                        ),
                      ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('返回修改'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: Text(summarizeOnly ? '发送并生成本机摘要' : '发送到云端生成'),
              ),
            ],
          ),
        ) ??
        false;
  }

  Json validateSummary(Json response) {
    final raw = response['summary'] is Map
        ? Map<String, dynamic>.from(response['summary'] as Map)
        : response;
    if (raw['schema_version'] != 1 ||
        raw['goal'] is! String ||
        utf8.encode(jsonEncode(raw)).length > 8000) {
      throw const LocalMemoryException('云端摘要格式无效，原对话和摘要仍保留');
    }
    final result = <String, dynamic>{'schema_version': 1, 'goal': raw['goal']};
    for (final key in [
      'constraints',
      'decisions',
      'completed',
      'open_questions',
      'next_actions',
    ]) {
      final value = raw[key] ?? [];
      if (value is! List ||
          value.any((item) => item is! String || item.length > 1000)) {
        throw const LocalMemoryException('云端摘要字段格式无效，原内容仍保留');
      }
      result[key] = value;
    }
    return result;
  }

  Future<void> saveConversation(
    List<Json> nextHistory,
    Json nextSummary,
    List<dynamic> nextSources,
  ) async {
    final List<Json> refs = chosen
        .map((row) => {'local_id': row.id, 'revision': row.revision})
        .toList();
    final storedRefs = conversation?.payload['memory_refs'] as List? ?? [];
    for (final ref in storedRefs) {
      if (!refs.any((row) => row['local_id'] == ref['local_id'])) {
        refs.add(Map<String, dynamic>.from(ref as Map));
      }
    }
    conversation = await session.store!.save(
      LocalRecordKind.conversation,
      {
        'history': nextHistory,
        'summary': nextSummary,
        'sources': nextSources,
        'memory_refs': refs,
      },
      id: conversation?.id,
      expectedRevision: conversation?.revision ?? 0,
      projectId: widget.projectId,
    );
  }

  Future<void> verifyLocalContext() async {
    final store = session.store;
    if (store == null) throw const LocalMemoryException('本机库已锁定');
    final currentMemories = await store.list(LocalRecordKind.memory);
    final now = DateTime.now();
    for (final chosenMemory in chosen) {
      if (!currentMemories.any(
        (row) =>
            row.id == chosenMemory.id &&
            row.revision == chosenMemory.revision &&
            memoryIsUsable(row, projectId: widget.projectId, now: now),
      )) {
        throw const LocalMemoryException('选中的记忆已变化，请返回记忆中心重新选择');
      }
    }
    if (widget.projectId != null) {
      final projects = await store.list(LocalRecordKind.project);
      if (!projects.any(
        (row) => row.id == widget.projectId && row.status == 'active',
      )) {
        throw const LocalMemoryException('项目已归档或移除，请重新选择');
      }
    }
    if (conversation != null) {
      final current = await store.list(LocalRecordKind.conversation);
      if (!current.any(
        (row) =>
            row.id == conversation!.id &&
            row.revision == conversation!.revision,
      )) {
        if (mounted) {
          setState(() {
            conversation = null;
            history = [];
            sources = [];
            summary = {'schema_version': 1, 'goal': ''};
          });
        }
        throw const LocalMemoryException('历史依赖的记忆已失效，旧对话上下文已清除；请检查后重新发送');
      }
    }
  }

  Future<void> summarize() async {
    if (!await review(summarizeOnly: true) || !mounted || !session.unlocked) {
      return;
    }
    final current = ++requestId;
    setState(() {
      generating = true;
      status = '验证账户并生成摘要…';
      error = '';
    });
    NativeAgentReadiness.setBlocked(this, true);
    try {
      await verifyLocalContext();
      await session.verifyOnline();
      final compressed = validateSummary(
        await session.transient.summarize(
          body(
            question.text.trim().isEmpty ? '压缩本机学习对话' : question.text.trim(),
          ),
        ),
      );
      if (!mounted || current != requestId || !session.unlocked) return;
      final recent = history
          .skip(history.length > 4 ? history.length - 4 : 0)
          .toList();
      await saveConversation(recent, compressed, sources);
      if (mounted && current == requestId) {
        setState(() {
          summary = compressed;
          history = recent;
          status = '摘要已加密保存于本机';
        });
      }
    } catch (failure) {
      if (mounted && current == requestId) {
        setState(() => error = failure.toString());
      }
    } finally {
      if (mounted && current == requestId) setState(() => generating = false);
      NativeAgentReadiness.setBlocked(this, question.text.isNotEmpty);
    }
  }

  Future<void> send() async {
    if (!await review(summarizeOnly: false) || !mounted || !session.unlocked) {
      return;
    }
    final current = ++requestId;
    final epoch = session.epoch;
    final prompt = question.text.trim();
    setState(() {
      generating = true;
      answer = '';
      status = '验证原生账户…';
      error = '';
    });
    NativeAgentReadiness.setBlocked(this, true);
    try {
      await verifyLocalContext();
      await session.verifyOnline();
      var nextSummary = summary;
      var recent = history;
      if (history.length >= 12) {
        setState(() => status = '压缩早期对话，保留固定要求…');
        nextSummary = validateSummary(
          await session.transient.summarize(body(prompt)),
        );
        recent = history.skip(history.length - 4).toList();
      }
      if (!mounted || current != requestId || epoch != session.epoch) return;
      await for (final event in session.transient.ask(
        body(prompt, messages: recent, compressed: nextSummary),
      )) {
        if (!mounted || current != requestId || epoch != session.epoch) return;
        if (event.type == 'status') {
          setState(
            () => status =
                event.data['message'] as String? ??
                event.data['stage'] as String? ??
                '生成中…',
          );
        }
        if (event.type == 'delta') {
          setState(
            () => answer +=
                event.data['text'] as String? ??
                event.data['delta'] as String? ??
                event.data['content'] as String? ??
                '',
          );
        }
        if (event.type == 'done') {
          final complete = event.data['answer'] as String;
          final nextSources = event.data['sources'] as List;
          final nextHistory = <Json>[
            ...recent,
            {'role': 'user', 'content': prompt},
            {'role': 'assistant', 'content': complete},
          ];
          await saveConversation(nextHistory, nextSummary, nextSources);
          if (!mounted || current != requestId || epoch != session.epoch) {
            return;
          }
          setState(() {
            history = nextHistory;
            summary = nextSummary;
            sources = nextSources;
            answer = '';
            status = '已加密保存于本机';
            question.clear();
          });
        }
      }
    } catch (failure) {
      if (mounted && current == requestId) {
        setState(() {
          error = failure.toString();
          status = '本轮未完成，输入仍保留';
          answer = '';
        });
      }
    } finally {
      if (mounted && current == requestId) setState(() => generating = false);
      NativeAgentReadiness.setBlocked(this, question.text.isNotEmpty);
    }
  }

  void cancel() {
    requestId++;
    session.transient.cancel();
    setState(() {
      generating = false;
      answer = '';
      status = '已停止，未完成内容未保存';
    });
    NativeAgentReadiness.setBlocked(this, question.text.isNotEmpty);
  }

  Future<void> leave() async {
    final approved =
        question.text.isEmpty && !generating ||
        (await showDialog<bool>(
              context: context,
              builder: (context) => AlertDialog(
                title: const Text('离开本机问答？'),
                content: const Text('将停止未完成生成并放弃未发送输入，已完成记录仍加密保存在本机。'),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('继续'),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('离开'),
                  ),
                ],
              ),
            )) ==
            true;
    if (approved && mounted) {
      setState(() => leaving = true);
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: leaving || (question.text.isEmpty && !generating),
    onPopInvokedWithResult: (didPop, _) {
      if (!didPop) leave();
    },
    child: Scaffold(
      appBar: AppBar(
        title: const Text('本机问答'),
        actions: [
          IconButton(
            tooltip: '压缩并保存本机摘要',
            onPressed: generating || history.isEmpty ? null : summarize,
            icon: const Icon(Icons.compress),
          ),
          IconButton(
            tooltip: '清除当前本机对话与摘要',
            onPressed: generating || conversation == null
                ? null
                : () async {
                    final approved = await showDialog<bool>(
                      context: context,
                      builder: (context) => AlertDialog(
                        title: const Text('清除本机对话？'),
                        content: const Text('此操作会删除当前对话和摘要，不影响记忆条目。'),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.pop(context, false),
                            child: const Text('取消'),
                          ),
                          FilledButton(
                            onPressed: () => Navigator.pop(context, true),
                            child: const Text('清除'),
                          ),
                        ],
                      ),
                    );
                    if (approved != true || !session.unlocked) return;
                    await session.store!.remove(
                      LocalRecordKind.conversation,
                      conversation!,
                    );
                    if (mounted) {
                      setState(() {
                        conversation = null;
                        history = [];
                        summary = {'schema_version': 1, 'goal': ''};
                        sources = [];
                      });
                    }
                  },
            icon: const Icon(Icons.delete_outline),
          ),
        ],
      ),
      body: busy
          ? const Center(child: CircularProgressIndicator())
          : Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 960),
                child: Column(
                  children: [
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(
                        '站点 ${session.api.server} · 原生账户 ${session.account!.username}\n对话、来源和摘要仅加密保存在本机；发送前可检查本轮载荷。',
                        textAlign: TextAlign.center,
                      ),
                    ),
                    if (error.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(
                          error,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ),
                    Expanded(
                      child: ListView(
                        padding: const EdgeInsets.all(16),
                        children: [
                          if ((summary['goal'] as String? ?? '').isNotEmpty)
                            ExpansionTile(
                              title: const Text('本机续接摘要'),
                              children: [
                                SelectableText(
                                  const JsonEncoder.withIndent(
                                    '  ',
                                  ).convert(summary),
                                ),
                              ],
                            ),
                          for (final row in history)
                            Card(
                              child: Padding(
                                padding: const EdgeInsets.all(16),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      row['role'] == 'user' ? '你' : 'WikiTide',
                                      style: const TextStyle(
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                    const SizedBox(height: 8),
                                    SelectableText(row['content'] as String),
                                  ],
                                ),
                              ),
                            ),
                          if (answer.isNotEmpty)
                            Card(
                              child: Padding(
                                padding: const EdgeInsets.all(16),
                                child: SelectableText(answer),
                              ),
                            ),
                          if (sources.isNotEmpty)
                            ExpansionTile(
                              title: Text('最近回答来源（${sources.length}）'),
                              children: sources
                                  .map(
                                    (row) => Padding(
                                      padding: const EdgeInsets.all(8),
                                      child: Text(
                                        row is Map
                                            ? '${row['title'] ?? row['note_id'] ?? ''} ${row['url'] ?? ''}'
                                            : row.toString(),
                                      ),
                                    ),
                                  )
                                  .toList(),
                            ),
                          ExpansionTile(
                            initiallyExpanded: true,
                            title: Text('待选本机记忆 · 已选 ${selected.length}/5'),
                            subtitle: const Text('关键词排序只在本机进行；勾选后才会进入发送预览。'),
                            children: candidates
                                .map(
                                  (row) => CheckboxListTile(
                                    value: selected.contains(row.id),
                                    onChanged: generating
                                        ? null
                                        : (value) => setState(() {
                                            if (value == true &&
                                                selected.length < 5) {
                                              selected.add(row.id);
                                            } else {
                                              selected.remove(row.id);
                                            }
                                          }),
                                    title: Text(
                                      row.payload['title'] as String? ?? '记忆',
                                    ),
                                    subtitle: Text(
                                      row.payload['content'] as String,
                                    ),
                                  ),
                                )
                                .toList(),
                          ),
                        ],
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        children: [
                          Wrap(
                            spacing: 16,
                            children: [
                              SizedBox(
                                width: 220,
                                child: TextField(
                                  controller: noteId,
                                  enabled: !generating,
                                  keyboardType: TextInputType.number,
                                  decoration: const InputDecoration(
                                    labelText: '公开笔记 ID（可选）',
                                  ),
                                ),
                              ),
                              SizedBox(
                                width: 200,
                                child: CheckboxListTile(
                                  value: webSearch,
                                  onChanged: generating
                                      ? null
                                      : (value) => setState(
                                          () => webSearch = value == true,
                                        ),
                                  title: const Text('联网搜索'),
                                ),
                              ),
                            ],
                          ),
                          TextField(
                            controller: question,
                            enabled: !generating,
                            minLines: 2,
                            maxLines: 5,
                            maxLength: 4000,
                            decoration: const InputDecoration(
                              labelText: '问题（发送前检查）',
                            ),
                          ),
                          Row(
                            children: [
                              Expanded(child: Text(status)),
                              if (generating)
                                TextButton(
                                  onPressed: cancel,
                                  child: const Text('停止'),
                                )
                              else
                                FilledButton.icon(
                                  onPressed: send,
                                  icon: const Icon(Icons.send_outlined),
                                  label: const Text('检查待发送内容'),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
    ),
  );
}
