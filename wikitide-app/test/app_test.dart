import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wikitide/api.dart';
import 'package:wikitide/app_state.dart';
import 'package:wikitide/main.dart';
import 'package:wikitide/models.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    FlutterSecureStorage.setMockInitialValues({});
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('wikitide/attachments'),
          (_) async => null,
        );
  });
  WikiApi api() => WikiApi(
    client: MockClient(
      (request) async => http.Response(
        jsonEncode(
          request.url.path.endsWith('home')
              ? {
                  'categories': [
                    {
                      'name': 'Frontend',
                      'path': 'Frontend',
                      'total': 1,
                      'children': [],
                    },
                  ],
                  'recent': [
                    {
                      'id': 1,
                      'title': 'Flutter 笔记',
                      'summary': '跨端阅读',
                      'category': 'Frontend',
                      'visibility': 'public',
                    },
                  ],
                  'continue_reading': [],
                }
              : request.url.path.endsWith('login')
              ? {
                  'token': 'test-token',
                  'user': {'id': 1, 'username': 'owner', 'role': 'admin'},
                }
              : {'ok': true},
        ),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      ),
    ),
  );

  testWidgets('home adapts to mobile and desktop without layout overflow', (
    tester,
  ) async {
    for (final width in [320.0, 390.0, 768.0, 1440.0]) {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      await tester.pumpWidget(
        WikiTideApp(
          key: ValueKey(width),
          state: AppState(api: api()),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('让知识\n有迹可循。'), findsOneWidget);
      expect(tester.takeException(), isNull, reason: 'width=$width');
      if (width < 900) {
        expect(find.text('探索'), findsOneWidget);
      } else {
        expect(find.text('探索首页'), findsOneWidget);
      }
    }
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
  });

  test(
    'drafts are account scoped and logout clears only that account',
    () async {
      final state = AppState(api: api());
      await state.initialize();
      state.account = const Account(1, 'owner', 'admin');
      state.api.token = 'test-token';
      await state.writeDraft(1, 'first private draft', 'sha1');
      state.account = const Account(2, 'reader', 'reader');
      expect(await state.readDraft(1), isNull);
      await state.writeDraft(1, 'second draft', 'sha2');
      await state.logout();
      state.account = const Account(2, 'reader', 'reader');
      expect(await state.readDraft(1), isNull);
      state.account = const Account(1, 'owner', 'admin');
      expect((await state.readDraft(1))?['body'], 'first private draft');
    },
  );
}
