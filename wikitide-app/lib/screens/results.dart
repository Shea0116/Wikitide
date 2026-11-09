import 'package:flutter/material.dart';
import '../app_state.dart';
import '../models.dart';
import '../widgets/common.dart';
import 'reading.dart';

class ResultsPage extends StatefulWidget {
  const ResultsPage({
    super.key,
    required this.state,
    this.category = '',
    this.query,
  });
  final AppState state;
  final String category;
  final String? query;
  @override
  State<ResultsPage> createState() => _ResultsPageState();
}

class _ResultsPageState extends State<ResultsPage> {
  int _page = 1;
  late Future<NotePage> _future = _load();
  late final _search = TextEditingController(text: widget.query ?? '');
  late String _query = widget.query ?? '';
  Future<NotePage> _load() => widget.query == null
      ? widget.state.api.browse(widget.category, _page)
      : widget.state.api.search(_query, widget.category, _page);
  void _reload() => setState(() => _future = _load());
  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(
        widget.query != null
            ? '搜索笔记'
            : widget.category.isEmpty
            ? '全部笔记'
            : widget.category,
      ),
      actions: [
        IconButton(
          tooltip: '刷新',
          onPressed: _reload,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: Column(
      children: [
        if (widget.query != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 6, 20, 12),
            child: TextField(
              controller: _search,
              maxLength: 100,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: '标题、摘要、正文',
                counterText: '',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: IconButton(
                  tooltip: '搜索',
                  icon: const Icon(Icons.arrow_forward),
                  onPressed: () {
                    _query = _search.text.trim();
                    _page = 1;
                    _reload();
                  },
                ),
              ),
              onSubmitted: (text) {
                _query = text.trim();
                _page = 1;
                _reload();
              },
            ),
          ),
        Expanded(
          child: AsyncPane(
            future: _future,
            retry: _reload,
            builder: (data) => RefreshIndicator(
              onRefresh: () async {
                _reload();
                await _future;
              },
              child: ListView(
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 12,
                ),
                children: [
                  Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 960),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            '共 ${data.total} 篇 · 第 $_page 页',
                            style: Theme.of(context).textTheme.labelLarge,
                          ),
                          const SizedBox(height: 12),
                          if (data.notes.isEmpty)
                            const EmptyState('没有找到笔记，试试其他关键词。'),
                          ...data.notes.map(
                            (note) => NoteTile(
                              note: note,
                              onTap: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => ReadingPage(
                                    state: widget.state,
                                    noteId: note.id,
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(height: 20),
                          Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              OutlinedButton(
                                onPressed: _page > 1
                                    ? () {
                                        _page--;
                                        _reload();
                                      }
                                    : null,
                                child: const Text('上一页'),
                              ),
                              const SizedBox(width: 16),
                              FilledButton(
                                onPressed: data.hasMore
                                    ? () {
                                        _page++;
                                        _reload();
                                      }
                                    : null,
                                child: const Text('下一页'),
                              ),
                            ],
                          ),
                          const SizedBox(height: 20),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    ),
  );
}
