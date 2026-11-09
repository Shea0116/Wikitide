import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import '../api.dart';
import '../app_state.dart';
import '../models.dart';
import '../reader_document.dart';
import '../widgets/common.dart';
import '../widgets/reader_view.dart';
import 'edit.dart';
import 'history.dart';
import 'login.dart';

class ReadingPage extends StatefulWidget {
  const ReadingPage({
    super.key,
    required this.state,
    required this.noteId,
    this.fragment,
  });
  final AppState state;
  final int noteId;
  final String? fragment;
  @override
  State<ReadingPage> createState() => _ReadingPageState();
}

class _ReadingPageState extends State<ReadingPage> with WidgetsBindingObserver {
  late Future<NoteDetail> _future = _load();
  late final int _epoch = widget.state.sessionEpoch;
  WebViewController? _reader;
  List<OutlineEntry> _outline = [];
  Timer? _timer;
  double _position = 0;
  bool _bookmarked = false, _bookmarkBusy = false, _attachmentBusy = false;
  String? _progressError;
  bool _hasPosition = false;
  Future<NoteDetail> _load() async {
    final data = await widget.state.api.note(widget.noteId);
    _bookmarked = data.bookmarked;
    _position = data.position;
    return data;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      _flushProgress();
    }
  }

  void _reload() => setState(() {
    _reader = null;
    _future = _load();
  });
  void _progress(double value) {
    _position = value;
    _hasPosition = true;
    _timer?.cancel();
    _timer = Timer(const Duration(seconds: 2), _flushProgress);
  }

  Future<void> _flushProgress() async {
    _timer?.cancel();
    if (!_hasPosition ||
        widget.state.account == null ||
        _epoch != widget.state.sessionEpoch) {
      return;
    }
    try {
      await widget.state.api.call(
        'PUT',
        '/notes/${widget.noteId}/reading',
        body: {'position': _position},
      );
      _hasPosition = false;
      if (mounted && _progressError != null) {
        setState(() => _progressError = null);
      }
    } catch (error) {
      if (mounted) setState(() => _progressError = '阅读进度未同步，点击重试');
    }
  }

  Future<void> _bookmark() async {
    if (widget.state.account == null) {
      await Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => LoginPage(state: widget.state)),
      );
      return;
    }
    if (_bookmarkBusy) return;
    setState(() => _bookmarkBusy = true);
    try {
      await widget.state.api.call(
        'PUT',
        '/notes/${widget.noteId}/bookmark',
        body: {'bookmarked': !_bookmarked},
      );
      if (mounted) setState(() => _bookmarked = !_bookmarked);
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _bookmarkBusy = false);
    }
  }

  Future<void> _link(String href) async {
    final target = Uri.parse(widget.state.api.server).resolve(href);
    final match = RegExp(r'^/n/(\d+)$').firstMatch(target.path);
    if (target.origin == Uri.parse(widget.state.api.server).origin &&
        match != null) {
      final id = int.parse(match[1]!);
      if (id == widget.noteId && target.fragment.isNotEmpty) {
        await _reader?.runJavaScript('jump(${jsonEncode(target.fragment)});');
      } else if (mounted) {
        await _flushProgress();
        if (!mounted) return;
        await Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => ReadingPage(
              state: widget.state,
              noteId: id,
              fragment: target.fragment,
            ),
          ),
        );
      }
      return;
    }
    final asset = assetUri(href, widget.state.api.server);
    if (asset != null) {
      if (_attachmentBusy) return;
      setState(() => _attachmentBusy = true);
      try {
        final response = await widget.state.api.asset(asset);
        if (!mounted || _epoch != widget.state.sessionEpoch) return;
        final name = (asset.queryParameters['path'] ?? 'attachment.pdf')
            .split('/')
            .last;
        await const MethodChannel('wikitide/attachments').invokeMethod<void>(
          'open',
          {'bytes': response.bodyBytes, 'name': name},
        );
      } catch (error) {
        if (mounted) showError(context, error);
      } finally {
        if (mounted) setState(() => _attachmentBusy = false);
      }
      return;
    }
    if (!{'https', 'http', 'mailto'}.contains(target.scheme)) {
      throw const ApiException('不支持打开这个链接');
    }
    if (!await launchUrl(target, mode: LaunchMode.externalApplication)) {
      throw const ApiException('无法打开链接');
    }
  }

  void _showOutline() => showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (context) => SafeArea(
      child: ListView(
        children: [
          const Padding(
            padding: EdgeInsets.all(20),
            child: Text('本文目录', style: TextStyle(fontSize: 22)),
          ),
          if (_outline.isEmpty) const EmptyState('本文没有章节标题。'),
          ..._outline.map(
            (entry) => ListTile(
              contentPadding: EdgeInsets.only(
                left: 20 + (entry.level - 1) * 12,
                right: 20,
              ),
              title: Text(entry.title),
              onTap: () {
                Navigator.pop(context);
                _reader?.runJavaScript('jump(${jsonEncode(entry.id)});');
              },
            ),
          ),
        ],
      ),
    ),
  );
  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _flushProgress();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('阅读'),
      actions: [
        IconButton(
          tooltip: _bookmarked ? '取消收藏' : '收藏',
          onPressed: _bookmarkBusy ? null : _bookmark,
          icon: Icon(_bookmarked ? Icons.bookmark : Icons.bookmark_border),
        ),
        IconButton(
          tooltip: '本文目录',
          onPressed: _reader == null ? null : _showOutline,
          icon: const Icon(Icons.format_list_bulleted),
        ),
        IconButton(
          tooltip: '刷新',
          onPressed: _reload,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: AsyncPane(
      future: _future,
      retry: _reload,
      builder: (data) => Column(
        children: [
          if (data.editable)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton.icon(
                    onPressed: () async {
                      await Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) =>
                              HistoryPage(state: widget.state, detail: data),
                        ),
                      );
                      if (mounted) _reload();
                    },
                    icon: const Icon(Icons.history, size: 18),
                    label: const Text('历史版本'),
                  ),
                  TextButton.icon(
                    onPressed: () async {
                      final saved = await Navigator.push<bool>(
                        context,
                        MaterialPageRoute(
                          builder: (_) =>
                              EditPage(state: widget.state, detail: data),
                        ),
                      );
                      if (saved == true && mounted) _reload();
                    },
                    icon: const Icon(Icons.edit_outlined, size: 18),
                    label: const Text('编辑'),
                  ),
                ],
              ),
            ),
          if (_progressError != null)
            TextButton.icon(
              onPressed: _flushProgress,
              icon: const Icon(Icons.cloud_off_outlined, size: 16),
              label: Text(_progressError!),
            ),
          if (_attachmentBusy) const LinearProgressIndicator(),
          Expanded(
            child: ReaderView(
              key: ValueKey(data.sha),
              state: widget.state,
              source: data.html,
              title: data.note.title,
              category: data.note.category,
              summary: data.note.summary,
              position: data.position,
              fragment: widget.fragment,
              onLink: _link,
              onProgress: _progress,
              onReady: (controller, outline) {
                if (mounted) {
                  setState(() {
                    _reader = controller;
                    _outline = outline;
                  });
                }
              },
            ),
          ),
          if (data.related.isNotEmpty || data.backlinks.isNotEmpty)
            SafeArea(
              top: false,
              child: SizedBox(
                height: 48,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    ...data.related.map(
                      (note) => Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ActionChip(
                          label: Text('相关 · ${note.title}'),
                          onPressed: () => _link('/n/${note.id}'),
                        ),
                      ),
                    ),
                    ...data.backlinks.map(
                      (note) => Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ActionChip(
                          label: Text('引用 · ${note.title}'),
                          onPressed: () => _link('/n/${note.id}'),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    ),
  );
}
