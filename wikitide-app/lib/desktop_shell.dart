import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

// Colors are the current website's hex.css tokens, including its dark theme.
class _Palette {
  const _Palette(this.dark);

  final bool dark;
  Color get wash => dark ? const Color(0xff1c1b18) : const Color(0xfff1ebdf);
  Color get paper => dark ? const Color(0xff26241f) : const Color(0xfffbf7ef);
  Color get ink => dark ? const Color(0xffe4dccd) : const Color(0xff28251f);
  Color get muted => dark ? const Color(0xffb0a592) : const Color(0xff746d61);
  Color get line => dark ? const Color(0xff4a4236) : const Color(0xffd4ccbd);
  Color get accent => dark ? const Color(0xffd1ad65) : const Color(0xffa67c32);
  Color get selected =>
      dark ? const Color(0xff393226) : const Color(0xffeae1d0);
}

class DesktopShell extends StatelessWidget {
  const DesktopShell({
    super.key,
    required this.content,
    required this.currentUri,
    required this.pageTitle,
    required this.loading,
    required this.canGoBack,
    required this.canGoForward,
    required this.dark,
    required this.sidebarCollapsed,
    required this.accountName,
    required this.isAdmin,
    required this.accountExpanded,
    required this.searchFocusNode,
    required this.canFindArticle,
    required this.findOpen,
    required this.findBusy,
    required this.findError,
    required this.findTotal,
    required this.findCurrent,
    required this.findController,
    required this.findFocusNode,
    required this.onOpenFind,
    required this.onCloseFind,
    required this.onFindChanged,
    required this.onFindNext,
    required this.onFindPrevious,
    required this.onNavigate,
    required this.onSearch,
    required this.onBack,
    required this.onForward,
    required this.onReload,
    required this.onToggleSidebar,
    required this.onToggleTheme,
    required this.onOpenBrowser,
    required this.onLogout,
    required this.onToggleAccount,
    required this.onCheckForUpdates,
    this.onOpenNativeMemory,
  });

  final Widget content;
  final Uri currentUri;
  final String pageTitle;
  final bool loading;
  final bool canGoBack;
  final bool canGoForward;
  final bool dark;
  final bool sidebarCollapsed;
  final String? accountName;
  final bool isAdmin;
  final bool accountExpanded;
  final FocusNode searchFocusNode;
  final bool canFindArticle;
  final bool findOpen;
  final bool findBusy;
  final String? findError;
  final int findTotal;
  final int findCurrent;
  final TextEditingController findController;
  final FocusNode findFocusNode;
  final VoidCallback onOpenFind;
  final VoidCallback onCloseFind;
  final ValueChanged<String> onFindChanged;
  final VoidCallback onFindNext;
  final VoidCallback onFindPrevious;
  final ValueChanged<String> onNavigate;
  final ValueChanged<String> onSearch;
  final VoidCallback onBack;
  final VoidCallback onForward;
  final VoidCallback onReload;
  final VoidCallback onToggleSidebar;
  final VoidCallback onToggleTheme;
  final VoidCallback onOpenBrowser;
  final VoidCallback onLogout;
  final VoidCallback onToggleAccount;
  final VoidCallback? onCheckForUpdates;
  final VoidCallback? onOpenNativeMemory;

  bool _selected(String path) {
    if (path == '/') return currentUri.path == '/';
    if (path == '/explore') {
      return currentUri.path == path || currentUri.path.startsWith('$path/');
    }
    if (path.startsWith('/browse?')) {
      return currentUri.path == '/browse' &&
          currentUri.queryParameters['category'] ==
              Uri.parse(path).queryParameters['category'];
    }
    if (path == '/browse') {
      return currentUri.path == '/browse' &&
          !currentUri.queryParameters.containsKey('category');
    }
    return currentUri.path == path;
  }

  Widget _sectionLabel(String label, _Palette colors) => Padding(
    padding: const EdgeInsets.fromLTRB(19, 23, 12, 9),
    child: Text(
      label,
      style: TextStyle(
        fontFamily: 'WikiTide Song',
        fontSize: 11,
        fontWeight: FontWeight.w600,
        letterSpacing: 1.1,
        color: colors.muted,
      ),
    ),
  );

  Widget _navItem(_Palette colors, String label, IconData icon, String path) {
    final selected = _selected(path);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
      child: Material(
        color: selected ? colors.selected : Colors.transparent,
        borderRadius: BorderRadius.circular(7),
        child: InkWell(
          borderRadius: BorderRadius.circular(7),
          hoverColor: colors.selected.withValues(alpha: 0.65),
          onTap: () => onNavigate(path),
          child: Container(
            height: 39,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border(
                left: BorderSide(
                  width: 2,
                  color: selected ? colors.accent : Colors.transparent,
                ),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected ? colors.ink : colors.muted,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontFamily: 'WikiTide Song',
                      fontSize: 14,
                      fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                      color: selected ? colors.ink : colors.muted,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _sidebar(_Palette colors) => Container(
    width: 242,
    decoration: BoxDecoration(
      color: colors.wash,
      border: Border(right: BorderSide(color: colors.line)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: ListView(
            padding: EdgeInsets.zero,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(22, 56, 22, 3),
                child: InkWell(
                  onTap: () => onNavigate('/'),
                  child: ColorFiltered(
                    // Matches hex.css: dark-mode brand-logo brightness(0) invert(.88).
                    colorFilter: colors.dark
                        ? const ColorFilter.mode(
                            Color(0xffe0e0e0),
                            BlendMode.srcIn,
                          )
                        : const ColorFilter.mode(Colors.white, BlendMode.dst),
                    child: Image.asset(
                      'assets/logo.png',
                      width: 155,
                      height: 62,
                      alignment: Alignment.centerLeft,
                    ),
                  ),
                ),
              ),
              _sectionLabel('工作区', colors),
              _navItem(colors, '首页', Icons.home_outlined, '/'),
              _navItem(colors, '全部笔记', Icons.menu_book_outlined, '/browse'),
              _navItem(colors, '知识求索', Icons.auto_awesome_outlined, '/explore'),
              _navItem(colors, '关系图谱', Icons.hub_outlined, '/graph'),
              if (accountName != null) ...[
                _navItem(colors, '团队空间', Icons.groups_outlined, '/teams'),
                _navItem(
                  colors,
                  '资料导入',
                  Icons.file_upload_outlined,
                  '/imports',
                ),
              ],
              _sectionLabel('知识方向', colors),
              _navItem(
                colors,
                'Backend',
                Icons.dns_outlined,
                '/browse?category=Backend',
              ),
              _navItem(
                colors,
                'Frontend',
                Icons.web_outlined,
                '/browse?category=Frontend',
              ),
              _navItem(
                colors,
                '知识导览',
                Icons.explore_outlined,
                '/browse?category=${Uri.encodeQueryComponent('知识导览')}',
              ),
              _sectionLabel('个人', colors),
              _navItem(colors, '我的阅读', Icons.bookmark_border, '/library'),
              if (onOpenNativeMemory != null)
                _accountAction(
                  colors,
                  Icons.memory_outlined,
                  '本机记忆',
                  onOpenNativeMemory!,
                ),
              if (accountName != null) ...[
                _navItem(colors, '学习与复习', Icons.school_outlined, '/learn'),
                _navItem(colors, '团队提及与待办', Icons.task_alt, '/team-inbox'),
              ],
              if (isAdmin) ...[
                _navItem(colors, '分享管理', Icons.share_outlined, '/admin/shares'),
                _navItem(
                  colors,
                  '知识求索质量',
                  Icons.insights_outlined,
                  '/admin/explore-quality',
                ),
              ],
            ],
          ),
        ),
        if (accountName != null && accountExpanded) ...[
          _accountAction(
            colors,
            Icons.bookmark_border,
            '我的阅读',
            () => onNavigate('/library'),
          ),
          _accountAction(
            colors,
            Icons.manage_accounts_outlined,
            '账户设置',
            () => onNavigate('/account'),
          ),
          if (isAdmin)
            _accountAction(
              colors,
              Icons.settings_outlined,
              '管理后台',
              () => onNavigate('/admin'),
            ),
          _accountAction(colors, Icons.logout, '退出登录', onLogout),
          const SizedBox(height: 8),
        ],
        Divider(height: 1, color: colors.line),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 13),
          child: Material(
            color: accountName == null ? colors.ink : colors.paper,
            borderRadius: BorderRadius.circular(6),
            child: InkWell(
              borderRadius: BorderRadius.circular(6),
              onTap: accountName == null
                  ? () => onNavigate('/login')
                  : onToggleAccount,
              child: Container(
                height: 45,
                padding: const EdgeInsets.symmetric(horizontal: 13),
                decoration: BoxDecoration(
                  border: accountName == null
                      ? null
                      : Border.all(color: colors.line),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  children: [
                    Icon(
                      accountName == null
                          ? Icons.login
                          : Icons.account_circle_outlined,
                      size: 18,
                      color: accountName == null ? colors.paper : colors.muted,
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        accountName ?? '登录账户',
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: accountName == null
                              ? colors.paper
                              : colors.ink,
                          fontFamily: 'WikiTide Song',
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    Icon(
                      accountName == null
                          ? Icons.arrow_forward
                          : accountExpanded
                          ? Icons.expand_less
                          : Icons.expand_more,
                      size: 16,
                      color: accountName == null ? colors.paper : colors.muted,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    ),
  );

  Widget _accountAction(
    _Palette colors,
    IconData icon,
    String label,
    VoidCallback action,
  ) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 1),
    child: Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: action,
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          child: Row(
            children: [
              Icon(icon, size: 16, color: colors.muted),
              const SizedBox(width: 12),
              Text(label, style: TextStyle(color: colors.ink, fontSize: 13)),
            ],
          ),
        ),
      ),
    ),
  );

  Widget _toolButton(
    _Palette colors,
    IconData icon,
    String tooltip,
    VoidCallback? onPressed,
  ) => IconButton(
    tooltip: tooltip,
    onPressed: onPressed,
    icon: Icon(icon, size: 18),
    color: colors.ink,
    disabledColor: colors.muted.withValues(alpha: 0.45),
    splashRadius: 19,
    visualDensity: VisualDensity.compact,
  );

  Widget _toolbar(_Palette colors, double width, bool showSidebar) => Container(
    height: 62,
    // In a full-size macOS window the traffic lights occupy the top-left
    // corner. The sidebar normally reserves it; reserve it here when hidden.
    padding: EdgeInsets.only(left: showSidebar ? 13 : 86, right: 13),
    decoration: BoxDecoration(
      color: colors.paper,
      border: Border(bottom: BorderSide(color: colors.line)),
    ),
    child: Row(
      children: [
        _toolButton(
          colors,
          Icons.view_sidebar_outlined,
          sidebarCollapsed ? '显示侧边栏（⌘B）' : '隐藏侧边栏（⌘B）',
          onToggleSidebar,
        ),
        const SizedBox(width: 6),
        Container(width: 1, height: 20, color: colors.line),
        const SizedBox(width: 6),
        _toolButton(
          colors,
          Icons.arrow_back,
          '后退（⌘[）',
          canGoBack ? onBack : null,
        ),
        _toolButton(
          colors,
          Icons.arrow_forward,
          '前进（⌘]）',
          canGoForward ? onForward : null,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            pageTitle,
            overflow: TextOverflow.ellipsis,
            maxLines: 1,
            style: TextStyle(
              fontFamily: 'WikiTide Song',
              fontWeight: FontWeight.w600,
              fontSize: 15,
              color: colors.ink,
            ),
          ),
        ),
        if (width >= 680) ...[
          SizedBox(
            width: width >= 1000 ? 300 : 230,
            height: 36,
            child: TextField(
              focusNode: searchFocusNode,
              onSubmitted: onSearch,
              style: TextStyle(fontSize: 13, color: colors.ink),
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: '搜索笔记  ⌘K',
                hintStyle: TextStyle(color: colors.muted, fontSize: 13),
                prefixIcon: Icon(Icons.search, size: 17, color: colors.muted),
                prefixIconConstraints: const BoxConstraints(minWidth: 35),
                filled: true,
                fillColor: colors.wash,
                contentPadding: const EdgeInsets.symmetric(vertical: 7),
                enabledBorder: OutlineInputBorder(
                  borderSide: BorderSide(color: colors.line),
                  borderRadius: BorderRadius.circular(6),
                ),
                focusedBorder: OutlineInputBorder(
                  borderSide: BorderSide(color: colors.accent),
                  borderRadius: BorderRadius.circular(6),
                ),
              ),
            ),
          ),
          const SizedBox(width: 9),
        ] else
          _toolButton(
            colors,
            Icons.search,
            '搜索笔记（⌘K）',
            () => onNavigate('/search'),
          ),
        _toolButton(
          colors,
          Icons.auto_awesome_outlined,
          '知识求索',
          () => onNavigate('/explore'),
        ),
        if (canFindArticle)
          _toolButton(
            colors,
            Icons.find_in_page_outlined,
            '文内查找（⌘F）',
            onOpenFind,
          ),
        _toolButton(colors, Icons.refresh, '刷新（⌘R）', onReload),
        if (onCheckForUpdates != null)
          _toolButton(
            colors,
            Icons.system_update_alt,
            '检查更新',
            onCheckForUpdates,
          ),
        _toolButton(
          colors,
          dark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
          dark ? '切换白天模式' : '切换黑夜模式',
          onToggleTheme,
        ),
        _toolButton(colors, Icons.open_in_browser, '在浏览器中打开', onOpenBrowser),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final colors = _Palette(dark);
    return LayoutBuilder(
      builder: (context, constraints) {
        final showSidebar = !sidebarCollapsed && constraints.maxWidth >= 760;
        final contentWidth = constraints.maxWidth - (showSidebar ? 242 : 0);
        return ColoredBox(
          color: colors.wash,
          child: Row(
            children: [
              if (showSidebar) _sidebar(colors),
              Expanded(
                child: Column(
                  children: [
                    _toolbar(colors, contentWidth, showSidebar),
                    if (findOpen)
                      ArticleFindBar(
                        dark: dark,
                        controller: findController,
                        focusNode: findFocusNode,
                        busy: findBusy,
                        error: findError,
                        total: findTotal,
                        current: findCurrent,
                        onChanged: onFindChanged,
                        onNext: onFindNext,
                        onPrevious: onFindPrevious,
                        onClose: onCloseFind,
                      ),
                    if (loading)
                      LinearProgressIndicator(
                        minHeight: 2,
                        color: colors.accent,
                        backgroundColor: colors.line,
                      )
                    else
                      const SizedBox(height: 2),
                    Expanded(child: content),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class ArticleFindBar extends StatelessWidget {
  const ArticleFindBar({
    super.key,
    required this.dark,
    required this.controller,
    required this.focusNode,
    required this.busy,
    required this.error,
    required this.total,
    required this.current,
    required this.onChanged,
    required this.onNext,
    required this.onPrevious,
    required this.onClose,
  });

  final bool dark;
  final TextEditingController controller;
  final FocusNode focusNode;
  final bool busy;
  final String? error;
  final int total;
  final int current;
  final ValueChanged<String> onChanged;
  final VoidCallback onNext;
  final VoidCallback onPrevious;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final colors = _Palette(dark);
    final canMove = !busy && error == null && total > 0;
    return CallbackShortcuts(
      bindings: {const SingleActivator(LogicalKeyboardKey.escape): onClose},
      child: Container(
        height: 52,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
        decoration: BoxDecoration(
          color: colors.paper,
          border: Border(bottom: BorderSide(color: colors.line)),
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: controller,
                focusNode: focusNode,
                onChanged: onChanged,
                onEditingComplete: () {},
                onSubmitted: (_) {
                  if (!canMove) return;
                  if (HardwareKeyboard.instance.isShiftPressed) {
                    onPrevious();
                  } else {
                    onNext();
                  }
                },
                textInputAction: TextInputAction.search,
                style: TextStyle(fontSize: 13, color: colors.ink),
                decoration: InputDecoration(
                  hintText: '在文章正文中查找',
                  hintStyle: TextStyle(color: colors.muted, fontSize: 13),
                  prefixIcon: Icon(Icons.search, size: 17, color: colors.muted),
                  prefixIconConstraints: const BoxConstraints(minWidth: 35),
                  filled: true,
                  fillColor: colors.wash,
                  contentPadding: const EdgeInsets.symmetric(vertical: 7),
                  enabledBorder: OutlineInputBorder(
                    borderSide: BorderSide(color: colors.line),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderSide: BorderSide(color: colors.accent),
                    borderRadius: BorderRadius.circular(6),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(
              width: MediaQuery.sizeOf(context).width < 600 ? 58 : 132,
              child: ValueListenableBuilder<TextEditingValue>(
                valueListenable: controller,
                builder: (context, value, _) => Semantics(
                  liveRegion: true,
                  child: Text(
                    error ??
                        (value.text.isEmpty
                            ? '输入关键词'
                            : busy
                            ? '查找中…'
                            : total == 0
                            ? '未找到匹配'
                            : '$current / $total'),
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: colors.muted, fontSize: 12),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton(
              tooltip: '上一个（⇧Enter / ⇧⌘G）',
              onPressed: canMove ? onPrevious : null,
              icon: const Icon(Icons.keyboard_arrow_up, size: 20),
              color: colors.ink,
              visualDensity: VisualDensity.compact,
            ),
            IconButton(
              tooltip: '下一个（Enter / ⌘G）',
              onPressed: canMove ? onNext : null,
              icon: const Icon(Icons.keyboard_arrow_down, size: 20),
              color: colors.ink,
              visualDensity: VisualDensity.compact,
            ),
            IconButton(
              tooltip: '关闭文内查找（Esc）',
              onPressed: onClose,
              icon: const Icon(Icons.close, size: 17),
              color: colors.ink,
              visualDensity: VisualDensity.compact,
            ),
          ],
        ),
      ),
    );
  }
}
