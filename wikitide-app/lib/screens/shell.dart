import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../app_state.dart';
import '../models.dart';
import '../widgets/common.dart';
import '../widgets/library_home.dart';
import '../design.dart';
import 'login.dart';
import 'reading.dart';
import 'results.dart';

class LibraryShell extends StatefulWidget {
  const LibraryShell({super.key, required this.state});
  final AppState state;
  @override
  State<LibraryShell> createState() => _LibraryShellState();
}

class _LibraryShellState extends State<LibraryShell> {
  int _tab = 0;
  late Future<Json> _home = widget.state.api.call('GET', '/home');
  Future<Json>? _library;
  final _search = TextEditingController();
  final _focus = FocusNode();
  bool _busy = false;
  void _reload() => setState(() {
    _home = widget.state.api.call('GET', '/home');
    _library = widget.state.account == null
        ? null
        : widget.state.api.call('GET', '/library');
  });
  void _select(int index) => setState(() {
    _tab = index;
    if (index == 2 && widget.state.account != null) {
      _library = widget.state.api.call('GET', '/library');
    }
  });
  void _openResults({String category = '', String? query}) => Navigator.push(
    context,
    MaterialPageRoute(
      builder: (_) =>
          ResultsPage(state: widget.state, category: category, query: query),
    ),
  );
  void _openNote(Note note) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ReadingPage(state: widget.state, noteId: note.id),
      ),
    );
    if (mounted) _reload();
  }

  void _login() => Navigator.push(
    context,
    MaterialPageRoute(builder: (_) => LoginPage(state: widget.state)),
  );
  @override
  void dispose() {
    _search.dispose();
    _focus.dispose();
    super.dispose();
  }

  Widget _categories(List<Category> categories, {bool compact = false}) =>
      Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ListTile(
            leading: const Icon(Icons.library_books_outlined),
            title: const Text('全部笔记'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => _openResults(),
          ),
          ...categories.map((category) => _category(category, compact)),
        ],
      );
  Widget _category(Category category, bool compact) {
    if (category.children.isEmpty) {
      return ListTile(
        dense: compact,
        title: Text(category.name),
        trailing: Text('${category.total}'),
        onTap: () => _openResults(category: category.path),
      );
    }
    return ExpansionTile(
      title: Text(category.name),
      trailing: Text('${category.total}'),
      children: [
        ListTile(
          dense: true,
          title: Text('浏览「${category.name}」全部'),
          onTap: () => _openResults(category: category.path),
        ),
        Padding(
          padding: const EdgeInsets.only(left: 12),
          child: Column(
            children: category.children
                .map((c) => _category(c, compact))
                .toList(),
          ),
        ),
      ],
    );
  }

  Widget _homeView(Json data) => LibraryHome(
    data: data,
    onCategory: (category) => _openResults(category: category),
    onNote: _openNote,
    onLibrary: () => _select(2),
    refresh: () async {
      _reload();
      await _home;
    },
    warning: widget.state.startupError,
  );
  Widget _readingView() {
    if (widget.state.account == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const EmptyState('登录后收藏笔记，并在不同设备上继续阅读。'),
            FilledButton(onPressed: _login, child: const Text('登录')),
          ],
        ),
      );
    }
    _library ??= widget.state.api.call('GET', '/library');
    return AsyncPane(
      future: _library!,
      retry: _reload,
      builder: (data) => RefreshIndicator(
        onRefresh: () async {
          _reload();
          await _library;
        },
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 960),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SectionTitle('我的收藏'),
                    if (notesFrom(data['bookmarks']).isEmpty)
                      const EmptyState('阅读时点亮收藏，即可在这里找到笔记。'),
                    ...notesFrom(data['bookmarks']).map(
                      (note) =>
                          NoteTile(note: note, onTap: () => _openNote(note)),
                    ),
                    const SectionTitle('最近阅读'),
                    if (notesFrom(data['recent']).isEmpty)
                      const EmptyState('你阅读过的笔记会出现在这里。'),
                    ...notesFrom(data['recent']).map(
                      (note) =>
                          NoteTile(note: note, onTap: () => _openNote(note)),
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

  Future<void> _serverDialog() async {
    var address = widget.state.api.server;
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('站点地址'),
        content: SizedBox(
          width: 430,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                initialValue: address,
                onChanged: (value) => address = value,
                keyboardType: TextInputType.url,
                autocorrect: false,
                decoration: const InputDecoration(labelText: 'HTTPS 地址'),
              ),
              const SizedBox(height: 16),
              const Text('切换站点会退出当前账户，并清理该账户的本机草稿。'),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, address),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (value == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.state.setServer(value);
      if (mounted) _reload();
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _logout() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('退出账户'),
        content: const Text('退出后会清理这个账户在本机保存的编辑草稿。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('退出'),
          ),
        ],
      ),
    );
    if (confirm != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.state.logout();
    } catch (error) {
      if (mounted) showError(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _settingsView() => ListView(
    padding: const EdgeInsets.all(24),
    children: [
      Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 700),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionTitle('账户'),
              Card(
                child: ListTile(
                  leading: const Icon(Icons.person_outline),
                  title: Text(widget.state.account?.username ?? '尚未登录'),
                  subtitle: Text(
                    widget.state.account == null ? '公开笔记可以直接阅读' : '已与网站账户连接',
                  ),
                  trailing: TextButton(
                    onPressed: _busy
                        ? null
                        : widget.state.account == null
                        ? _login
                        : _logout,
                    child: Text(widget.state.account == null ? '登录' : '退出'),
                  ),
                ),
              ),
              const SectionTitle('阅读偏好'),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('外观'),
                      const SizedBox(height: 14),
                      SegmentedButton<ThemeMode>(
                        segments: const [
                          ButtonSegment(
                            value: ThemeMode.system,
                            label: Text('跟随系统'),
                          ),
                          ButtonSegment(
                            value: ThemeMode.light,
                            label: Text('浅色'),
                          ),
                          ButtonSegment(
                            value: ThemeMode.dark,
                            label: Text('深色'),
                          ),
                        ],
                        selected: {widget.state.theme},
                        onSelectionChanged: (value) =>
                            widget.state.setTheme(value.first),
                      ),
                      const SizedBox(height: 24),
                      Text('正文字号 · ${widget.state.fontSize.round()}'),
                      Slider(
                        value: widget.state.fontSize,
                        min: 14,
                        max: 24,
                        divisions: 10,
                        label: '${widget.state.fontSize.round()}',
                        onChanged: widget.state.setFontSize,
                      ),
                    ],
                  ),
                ),
              ),
              const SectionTitle('连接'),
              Card(
                child: ListTile(
                  leading: const Icon(Icons.cloud_outlined),
                  title: const Text('WikiTide 站点'),
                  subtitle: Text(widget.state.api.server),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: _busy ? null : _serverDialog,
                ),
              ),
              const SizedBox(height: 26),
              const Text(
                '识汐 WikiTide · 0.1.0\n正文与权限由服务器管理。草稿只保存在本机。',
                textAlign: TextAlign.center,
              ),
              if (_busy)
                const Padding(
                  padding: EdgeInsets.all(20),
                  child: Center(child: CircularProgressIndicator()),
                ),
            ],
          ),
        ),
      ),
    ],
  );

  @override
  Widget build(BuildContext context) => CallbackShortcuts(
    bindings: {
      const SingleActivator(LogicalKeyboardKey.keyK, meta: true): () =>
          _focus.requestFocus(),
      const SingleActivator(LogicalKeyboardKey.keyK, control: true): () =>
          _focus.requestFocus(),
    },
    child: Focus(
      autofocus: true,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final desktop = constraints.maxWidth >= 900;
          final d = WikiDesign(context);
          return Scaffold(
            body: SafeArea(
              bottom: false,
              child: Column(
                children: [
                  _header(desktop),
                  Expanded(
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 1600),
                        child: Row(
                          children: [
                            if (desktop)
                              SizedBox(
                                width: 220,
                                child: DecoratedBox(
                                  decoration: BoxDecoration(
                                    border: Border(
                                      right: BorderSide(color: d.line),
                                    ),
                                  ),
                                  child: ListView(
                                    padding: const EdgeInsets.fromLTRB(
                                      22,
                                      54,
                                      22,
                                      30,
                                    ),
                                    children: [
                                      Padding(
                                        padding: const EdgeInsets.only(
                                          left: 12,
                                          bottom: 26,
                                        ),
                                        child: Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              'LIBRARY INDEX',
                                              style: d.mono(9),
                                            ),
                                            const SizedBox(height: 12),
                                            Text('知识地图', style: d.kai(23)),
                                          ],
                                        ),
                                      ),
                                      FutureBuilder<Json>(
                                        future: _home,
                                        builder: (context, snapshot) =>
                                            snapshot.hasData
                                            ? _categories(
                                                (snapshot.data!['categories']
                                                        as List)
                                                    .map(
                                                      (item) =>
                                                          Category.fromJson(
                                                            item as Json,
                                                          ),
                                                    )
                                                    .toList(),
                                                compact: true,
                                              )
                                            : ListTile(
                                                title: const Text('全部笔记'),
                                                onTap: () => _openResults(),
                                              ),
                                      ),
                                      const SizedBox(height: 36),
                                      Divider(color: d.line),
                                      const SizedBox(height: 12),
                                      for (final (index, title, icon) in [
                                        (0, '探索首页', Icons.explore_outlined),
                                        (2, '我的阅读', Icons.bookmark_border),
                                        (3, '设置', Icons.settings_outlined),
                                      ])
                                        ListTile(
                                          dense: true,
                                          leading: Icon(
                                            icon,
                                            size: 16,
                                            color: d.muted,
                                          ),
                                          title: Text(
                                            title,
                                            style: TextStyle(
                                              fontSize: 12,
                                              color: d.muted,
                                            ),
                                          ),
                                          onTap: () => _select(index),
                                        ),
                                    ],
                                  ),
                                ),
                              ),
                            Expanded(
                              child: Column(
                                children: [
                                  if (!desktop && _tab == 0)
                                    FutureBuilder<Json>(
                                      future: _home,
                                      builder: (context, snapshot) =>
                                          snapshot.hasData
                                          ? Container(
                                              height: 49,
                                              decoration: BoxDecoration(
                                                border: Border(
                                                  bottom: BorderSide(
                                                    color: d.line,
                                                  ),
                                                ),
                                              ),
                                              child: ListView(
                                                scrollDirection:
                                                    Axis.horizontal,
                                                padding:
                                                    const EdgeInsets.symmetric(
                                                      horizontal: 16,
                                                    ),
                                                children: [
                                                  TextButton(
                                                    onPressed: () =>
                                                        _openResults(),
                                                    child: const Text('全部笔记'),
                                                  ),
                                                  for (final category
                                                      in snapshot
                                                              .data!['categories']
                                                          as List)
                                                    TextButton(
                                                      onPressed: () =>
                                                          _openResults(
                                                            category:
                                                                category['path']
                                                                    as String,
                                                          ),
                                                      child: Text(
                                                        category['name']
                                                            as String,
                                                      ),
                                                    ),
                                                ],
                                              ),
                                            )
                                          : const SizedBox(),
                                    ),
                                  Expanded(
                                    child: switch (_tab) {
                                      0 => AsyncPane(
                                        future: _home,
                                        retry: _reload,
                                        builder: _homeView,
                                      ),
                                      1 => AsyncPane(
                                        future: _home,
                                        retry: _reload,
                                        builder: (data) => ListView(
                                          padding: const EdgeInsets.all(24),
                                          children: [
                                            const SectionTitle('知识地图'),
                                            _categories(
                                              (data['categories'] as List)
                                                  .map(
                                                    (item) => Category.fromJson(
                                                      item as Json,
                                                    ),
                                                  )
                                                  .toList(),
                                            ),
                                          ],
                                        ),
                                      ),
                                      2 => _readingView(),
                                      _ => _settingsView(),
                                    },
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            bottomNavigationBar: desktop
                ? null
                : Container(
                    decoration: BoxDecoration(
                      color: d.surface,
                      border: Border(top: BorderSide(color: d.line)),
                    ),
                    child: SafeArea(
                      top: false,
                      child: Row(
                        children: [
                          for (final (index, title, icon) in [
                            (0, '探索', Icons.explore_outlined),
                            (1, '分类', Icons.account_tree_outlined),
                            (2, '阅读', Icons.bookmark_border),
                            (3, '设置', Icons.settings_outlined),
                          ])
                            Expanded(
                              child: InkWell(
                                onTap: () => _select(index),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 10,
                                  ),
                                  child: Column(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(
                                        icon,
                                        size: 19,
                                        color: _tab == index
                                            ? d.accent
                                            : d.muted,
                                      ),
                                      const SizedBox(height: 5),
                                      Text(
                                        title,
                                        style: d
                                            .kai(14)
                                            .copyWith(
                                              color: _tab == index
                                                  ? d.accent
                                                  : d.muted,
                                            ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
          );
        },
      ),
    ),
  );

  Widget _header(bool desktop) {
    final d = WikiDesign(context);
    final logo = InkWell(
      onTap: () => _select(0),
      child: ColorFiltered(
        colorFilter: d.dark
            ? const ColorFilter.mode(Color(0xffeee8f5), BlendMode.srcIn)
            : const ColorFilter.mode(Colors.transparent, BlendMode.dst),
        child: Image.asset(
          'assets/logo.png',
          width: desktop
              ? 174
              : MediaQuery.sizeOf(context).width < 360
              ? 124
              : 150,
          height: 66,
          fit: BoxFit.contain,
        ),
      ),
    );
    final controls = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        TextButton(
          onPressed: () =>
              widget.state.setTheme(d.dark ? ThemeMode.light : ThemeMode.dark),
          style: TextButton.styleFrom(
            backgroundColor: d.hover,
            minimumSize: const Size(42, 42),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                d.dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
                size: 17,
              ),
              if (desktop) ...[
                const SizedBox(width: 8),
                Text(
                  d.dark ? '白天' : '黑夜',
                  style: const TextStyle(fontSize: 12),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(width: 10),
        OutlinedButton(
          onPressed: widget.state.account == null ? _login : () => _select(3),
          style: OutlinedButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 17, vertical: 10),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(widget.state.account?.username ?? '登录', style: d.kai(20)),
              const SizedBox(width: 12),
              const Icon(Icons.arrow_forward, size: 14),
            ],
          ),
        ),
      ],
    );
    return Container(
      decoration: BoxDecoration(
        color: d.surface,
        border: Border(bottom: BorderSide(color: d.line)),
      ),
      padding: EdgeInsets.symmetric(
        horizontal: desktop ? 40 : 16,
        vertical: desktop ? 0 : 10,
      ),
      child: desktop
          ? SizedBox(
              height: 88,
              child: Row(
                children: [
                  logo,
                  const SizedBox(width: 32),
                  Expanded(
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 420),
                        child: _searchField(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 32),
                  controls,
                ],
              ),
            )
          : Column(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [logo, controls],
                ),
                const SizedBox(height: 8),
                _searchField(),
              ],
            ),
    );
  }

  Widget _searchField() {
    final d = WikiDesign(context);
    return Container(
      height: 48,
      padding: const EdgeInsets.fromLTRB(15, 4, 5, 4),
      decoration: BoxDecoration(
        color: d.dark ? const Color(0xff211a2d) : const Color(0xfffbf9fe),
        border: Border.all(color: d.line),
        borderRadius: BorderRadius.circular(99),
      ),
      child: Row(
        children: [
          Icon(Icons.search, size: 16, color: d.muted),
          const SizedBox(width: 10),
          Expanded(
            child: TextField(
              controller: _search,
              focusNode: _focus,
              maxLength: 100,
              style: TextStyle(fontSize: 13, color: d.ink),
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                hintText: '在知识里，找一个答案',
                counterText: '',
                isDense: true,
                filled: false,
                contentPadding: EdgeInsets.zero,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
              ),
              onSubmitted: (value) => _openResults(query: value.trim()),
            ),
          ),
          TextButton(
            onPressed: () => _openResults(query: _search.text.trim()),
            style: TextButton.styleFrom(
              backgroundColor: d.hover,
              minimumSize: const Size(82, 38),
              padding: const EdgeInsets.symmetric(horizontal: 12),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('搜索', style: d.kai(19)),
                const SizedBox(width: 8),
                const Icon(Icons.arrow_forward, size: 14),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
