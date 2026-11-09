import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../api.dart';
import '../app_state.dart';
import '../models.dart';
import '../widgets/common.dart';
import '../widgets/reader_view.dart';

class EditPage extends StatefulWidget {
  const EditPage({super.key, required this.state, required this.detail});
  final AppState state;
  final NoteDetail detail;
  @override
  State<EditPage> createState() => _EditPageState();
}

class _EditPageState extends State<EditPage> with WidgetsBindingObserver {
  late final _body = TextEditingController(text: widget.detail.body);
  late String _sha = widget.detail.sha;
  late final String _draftKey =
      '${widget.state.draftPrefix}${widget.detail.note.id}';
  late final Future<void> _ready = _recover();
  Timer? _timer;
  Future<void> _draftQueue = Future.value();
  String? _draftError;
  String _status = '编辑草稿只保存在本机';
  bool _busy = false, _dirty = false, _mayPop = false, _saved = false;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _body.addListener(_changed);
  }

  Future<void> _recover() async {
    try {
      final raw = await widget.state.vault.read(key: _draftKey);
      if (raw == null || !mounted) return;
      final draft = jsonDecode(raw) as Json;
      if (draft['body'] == widget.detail.body) return;
      final restore = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('发现本机草稿'),
          content: Text(
            draft['base_sha'] == _sha
                ? '上次的编辑尚未提交，是否恢复？'
                : '草稿保存后网站正文已更新。恢复草稿后需要检查并合并最新正文。',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('使用网站正文'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('恢复草稿'),
            ),
          ],
        ),
      );
      if (!mounted) return;
      if (restore == true) {
        _sha = draft['base_sha'] as String;
        _body.text = draft['body'] as String;
      } else if (restore == false) {
        await widget.state.vault.delete(key: _draftKey);
      }
    } catch (_) {
      if (mounted) setState(() => _draftError = '本机草稿无法读取，请保留或复制当前正文');
    }
  }

  void _changed() {
    if (_saved) return;
    setState(() {
      _dirty = _body.text != widget.detail.body || _sha != widget.detail.sha;
      _status = '正在编辑…';
    });
    _timer?.cancel();
    _timer = Timer(const Duration(seconds: 1), _stash);
  }

  Future<void> _stash() {
    _timer?.cancel();
    if (!_dirty || _saved) return _draftQueue;
    final snapshot = jsonEncode({'body': _body.text, 'base_sha': _sha});
    _draftQueue = _draftQueue.then((_) async {
      try {
        await widget.state.vault.write(key: _draftKey, value: snapshot);
        _draftError = null;
        if (mounted) setState(() => _status = '本机草稿已保存');
      } catch (_) {
        _draftError = '草稿保存失败，请先复制正文备份';
        if (mounted) setState(() {});
      }
    });
    return _draftQueue;
  }

  Future<void> _save() async {
    if (_busy || _body.text.trim().isEmpty) return;
    setState(() => _busy = true);
    try {
      await widget.state.api.call(
        'PUT',
        '/notes/${widget.detail.note.id}',
        body: {'body': _body.text, 'base_sha': _sha},
      );
      _saved = true;
      _timer?.cancel();
      await _draftQueue;
      await widget.state.vault.delete(key: _draftKey);
      if (mounted) {
        setState(() {
          _dirty = false;
          _mayPop = true;
        });
        Navigator.pop(context, true);
      }
    } on ApiException catch (error) {
      if (error.status == 409 && error.data?['latest'] is Map && mounted) {
        await _conflict(
          Map<String, dynamic>.from(error.data!['latest'] as Map),
        );
      } else if (mounted) {
        showError(context, error);
      }
      await _stash();
    } catch (error) {
      if (mounted) showError(context, error);
      await _stash();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _conflict(Json latest) async {
    final reviewed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('网站正文已更新'),
        content: SizedBox(
          width: 720,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('你的输入已保留。请对照下面的最新正文，返回编辑器手动合并后再保存。'),
              const SizedBox(height: 16),
              Flexible(
                child: SingleChildScrollView(
                  child: SelectableText(latest['body'] as String),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('暂不合并'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('已查看，返回手动合并'),
          ),
        ],
      ),
    );
    if (reviewed == true && mounted) {
      setState(() {
        _sha = latest['body_sha'] as String;
        _dirty = true;
        _status = '请手动合并最新正文后保存';
      });
    }
  }

  Future<void> _leave() async {
    if (_busy) return;
    if (!_dirty) {
      setState(() => _mayPop = true);
      Navigator.pop(context);
      return;
    }
    await _stash();
    if (!mounted) return;
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('离开编辑器'),
        content: Text(
          _draftError == null
              ? '修改尚未提交。草稿已保存在本机，下次编辑可以恢复。'
              : '本机草稿保存失败，请返回编辑器复制正文备份。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('继续编辑'),
          ),
          if (_draftError == null)
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('保留草稿并离开'),
            ),
        ],
      ),
    );
    if (leave == true && mounted) {
      setState(() => _mayPop = true);
      Navigator.pop(context);
    }
  }

  Future<void> _preview() async {
    setState(() => _busy = true);
    try {
      final result = await widget.state.api.call(
        'POST',
        '/notes/${widget.detail.note.id}/preview',
        body: {'body': _body.text},
      );
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => Scaffold(
            appBar: AppBar(title: const Text('未提交的预览')),
            body: ReaderView(
              state: widget.state,
              source: result['html'] as String,
              title: widget.detail.note.title,
              category: widget.detail.note.category,
              onLink: (_) async {},
            ),
          ),
        ),
      );
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      _stash();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _stash();
    _body.removeListener(_changed);
    _body.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: _mayPop,
    onPopInvokedWithResult: (didPop, _) {
      if (!didPop) _leave();
    },
    child: Scaffold(
      appBar: AppBar(
        title: const Text('编辑笔记'),
        actions: [
          IconButton(
            tooltip: '复制正文备份',
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: _body.text));
              if (context.mounted) {
                ScaffoldMessenger.of(
                  context,
                ).showSnackBar(const SnackBar(content: Text('正文已复制')));
              }
            },
            icon: const Icon(Icons.copy_outlined),
          ),
          TextButton(
            onPressed: _busy ? null : _preview,
            child: const Text('预览'),
          ),
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton(
              onPressed: _busy ? null : _save,
              child: const Text('保存'),
            ),
          ),
        ],
      ),
      body: FutureBuilder<void>(
        future: _ready,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        _draftError ?? _status,
                        style: TextStyle(
                          color: _draftError == null
                              ? null
                              : Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                    if (_busy)
                      const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: TextField(
                    controller: _body,
                    enabled: !_busy,
                    expands: true,
                    minLines: null,
                    maxLines: null,
                    textAlignVertical: TextAlignVertical.top,
                    keyboardType: TextInputType.multiline,
                    autocorrect: false,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 15,
                      height: 1.7,
                    ),
                    decoration: const InputDecoration(
                      hintText: 'Markdown 正文',
                      contentPadding: EdgeInsets.all(18),
                    ),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    ),
  );
}
