import 'package:flutter/material.dart';
import '../app_state.dart';
import '../models.dart';
import '../widgets/common.dart';

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key, required this.state, required this.detail});
  final AppState state;
  final NoteDetail detail;
  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  late Future<Json> _future = _load();
  bool _busy = false;
  Future<Json> _load() =>
      widget.state.api.call('GET', '/notes/${widget.detail.note.id}/history');
  String _time(int timestamp) {
    final time = DateTime.fromMillisecondsSinceEpoch(
      timestamp * 1000,
    ).toLocal();
    return '${time.year}-${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} ${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';
  }

  Future<void> _inspect(Json revision) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final result = await widget.state.api.call(
        'GET',
        '/notes/${widget.detail.note.id}/history/${revision['id']}',
      );
      if (!mounted) return;
      final restore = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text('版本 · ${_time(revision['created_at'] as int)}'),
          content: SizedBox(
            width: 700,
            child: SingleChildScrollView(
              child: SelectableText(result['body'] as String),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('关闭'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('恢复此版本'),
            ),
          ],
        ),
      );
      if (restore != true || !mounted) return;
      final confirm = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('确认恢复'),
          content: const Text('将此正文保存为一个新版本，并加入现有同步队列。当前版本仍保留在历史中。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('恢复'),
            ),
          ],
        ),
      );
      if (confirm != true) return;
      await widget.state.api.call(
        'POST',
        '/notes/${widget.detail.note.id}/restore',
        body: {'revision_id': revision['id'], 'base_sha': widget.detail.sha},
      );
      if (mounted) Navigator.pop(context);
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('历史版本')),
    body: AsyncPane(
      future: _future,
      retry: () => setState(() => _future = _load()),
      builder: (data) => ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (_busy) const LinearProgressIndicator(),
          const Padding(
            padding: EdgeInsets.only(bottom: 20),
            child: Text('最近 100 个版本 · 点击查看正文或恢复'),
          ),
          for (final revision in data['revisions'] as List)
            Card(
              child: ListTile(
                leading: const Icon(Icons.history),
                title: Text(_time(revision['created_at'] as int)),
                subtitle: Text('${revision['actor']} · ${revision['origin']}'),
                trailing: const Icon(Icons.chevron_right),
                onTap: _busy ? null : () => _inspect(revision as Json),
              ),
            ),
        ],
      ),
    ),
  );
}
