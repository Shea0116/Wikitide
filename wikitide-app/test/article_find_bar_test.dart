import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wikitide/desktop_shell.dart';

void main() {
  late TextEditingController controller;
  late FocusNode focus;
  late int next;
  late int previous;
  late int closed;

  setUp(() {
    controller = TextEditingController(text: '关键词');
    focus = FocusNode();
    next = previous = closed = 0;
  });
  tearDown(() {
    controller.dispose();
    focus.dispose();
  });

  Future<void> showBar(
    WidgetTester tester, {
    int total = 3,
    bool busy = false,
    String? error,
    bool dark = false,
  }) => tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: ArticleFindBar(
          dark: dark,
          controller: controller,
          focusNode: focus,
          busy: busy,
          error: error,
          total: total,
          current: total == 0 ? 0 : 1,
          onChanged: (_) {},
          onNext: () => next++,
          onPrevious: () => previous++,
          onClose: () => closed++,
        ),
      ),
    ),
  );

  testWidgets('match navigation and Enter keep the search field focused', (
    tester,
  ) async {
    await showBar(tester);
    expect(find.text('1 / 3'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.keyboard_arrow_down));
    await tester.tap(find.byIcon(Icons.keyboard_arrow_up));
    expect(next, 1);
    expect(previous, 1);

    await tester.tap(find.byType(TextField));
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pump();
    expect(next, 2);
    expect(focus.hasFocus, true);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
    expect(previous, 2);
    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    expect(closed, 1);
  });

  testWidgets('empty, pending, missing and failed queries disable navigation', (
    tester,
  ) async {
    for (final state in [
      (total: 0, busy: false, error: null, label: '未找到匹配'),
      (total: 3, busy: true, error: null, label: '查找中…'),
      (total: 0, busy: false, error: '查找失败，请重试', label: '查找失败，请重试'),
    ]) {
      await showBar(
        tester,
        total: state.total,
        busy: state.busy,
        error: state.error,
      );
      expect(find.text(state.label), findsOneWidget);
      await tester.tap(find.byIcon(Icons.keyboard_arrow_down));
      await tester.tap(find.byIcon(Icons.keyboard_arrow_up));
      expect(next, 0);
      expect(previous, 0);
    }
    await showBar(tester, total: 0);
    controller.clear();
    await tester.pump();
    expect(find.text('输入关键词'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.close));
    expect(closed, 1);
  });

  testWidgets('find bar fits the desktop content width in both themes', (
    tester,
  ) async {
    // The macOS minimum window width is 760, with a 242px sidebar.
    tester.view.physicalSize = const Size(518, 620);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    for (final dark in [false, true]) {
      await showBar(tester, dark: dark);
      expect(tester.takeException(), isNull);
      expect(find.byType(TextField), findsOneWidget);
    }
  });
}
