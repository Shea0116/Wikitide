import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wikitide/desktop_shell.dart';

void main() {
  testWidgets('new tools remain reachable in a short desktop window', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1000, 560));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final searchFocus = FocusNode();
    final findFocus = FocusNode();
    final findController = TextEditingController();
    addTearDown(searchFocus.dispose);
    addTearDown(findFocus.dispose);
    addTearDown(findController.dispose);
    String? route;
    await tester.pumpWidget(
      MaterialApp(
        home: Material(
          child: DesktopShell(
            content: const SizedBox.expand(),
            currentUri: Uri.parse('https://wikitide.soulshea.vip/library'),
            pageTitle: '我的阅读',
            loading: false,
            canGoBack: false,
            canGoForward: false,
            dark: false,
            sidebarCollapsed: false,
            accountName: 'tester',
            isAdmin: true,
            accountExpanded: true,
            searchFocusNode: searchFocus,
            canFindArticle: false,
            findOpen: false,
            findBusy: false,
            findError: null,
            findTotal: 0,
            findCurrent: 0,
            findController: findController,
            findFocusNode: findFocus,
            onOpenFind: () {},
            onCloseFind: () {},
            onFindChanged: (_) {},
            onFindNext: () {},
            onFindPrevious: () {},
            onNavigate: (value) => route = value,
            onSearch: (_) {},
            onBack: () {},
            onForward: () {},
            onReload: () {},
            onToggleSidebar: () {},
            onToggleTheme: () {},
            onOpenBrowser: () {},
            onLogout: () {},
            onToggleAccount: () {},
            onCheckForUpdates: () {},
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.scrollUntilVisible(
      find.text('学习与复习'),
      160,
      scrollable: find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.tap(find.text('学习与复习'));
    expect(route, '/learn');
    await tester.tap(find.text('账户设置'));
    expect(route, '/account');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
