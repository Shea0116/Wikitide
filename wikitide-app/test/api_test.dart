import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:wikitide/api.dart';

void main() {
  test(
    'server validation excludes credentials, paths, insecure remote hosts',
    () {
      expect(normalizeServer(' https://example.com/ '), 'https://example.com');
      expect(
        normalizeServer('http://127.0.0.1:8765', development: true),
        'http://127.0.0.1:8765',
      );
      for (final value in [
        'http://example.com',
        'https://u:p@example.com',
        'https://example.com/path',
        'file:///tmp',
        'https://example.com?q=1',
      ]) {
        expect(
          () => normalizeServer(value, development: false),
          throwsA(isA<ApiException>()),
        );
      }
      expect(
        () => normalizeServer('http://localhost', development: false),
        throwsA(isA<ApiException>()),
      );
    },
  );

  test(
    'native API sends bearer auth and UTF-8 JSON without following redirects',
    () async {
      final api = WikiApi(
        server: 'https://example.com',
        client: MockClient((request) async {
          expect(
            request.url.toString(),
            'https://example.com/api/client/v1/notes/1',
          );
          expect(request.followRedirects, false);
          expect(request.headers['Authorization'], 'Bearer test-token');
          expect(jsonDecode(request.body)['body'], '中文正文');
          return http.Response.bytes(utf8.encode('{"title":"中文"}'), 200);
        }),
      )..token = 'test-token';
      expect(
        (await api.call('PUT', '/notes/1', body: {'body': '中文正文'}))['title'],
        '中文',
      );
      api.close();
    },
  );

  test(
    'expired auth is reported and conflict body remains available to merge',
    () async {
      int expired = 0;
      final api =
          WikiApi(
              client: MockClient(
                (request) async => http.Response(
                  request.url.path.endsWith('session')
                      ? '{"detail":"expired"}'
                      : '{"detail":"conflict","latest":{"body":"new"}}',
                  request.url.path.endsWith('session') ? 401 : 409,
                ),
              ),
            )
            ..token = 'test-token'
            ..onExpired = () => expired++;
      await expectLater(
        api.call('GET', '/session'),
        throwsA(isA<ApiException>().having((e) => e.status, 'status', 401)),
      );
      expect(expired, 1);
      await expectLater(
        api.call('PUT', '/notes/1'),
        throwsA(
          isA<ApiException>().having(
            (e) => e.data?['latest']['body'],
            'latest body',
            'new',
          ),
        ),
      );
      expect(expired, 1);
    },
  );

  test(
    'login does not send an existing session to its credential endpoint',
    () async {
      final api = WikiApi(
        client: MockClient((request) async {
          expect(request.headers.containsKey('Authorization'), false);
          return http.Response('{"detail":"bad password"}', 401);
        }),
      )..token = 'old-token';
      bool expired = false;
      api.onExpired = () => expired = true;
      await expectLater(
        api.call('POST', '/login', authenticated: false),
        throwsA(isA<ApiException>()),
      );
      expect(expired, false);
    },
  );

  test(
    'assets cannot send credentials to another origin, path or redirect',
    () async {
      int requests = 0;
      final api = WikiApi(
        server: 'https://example.com',
        client: MockClient((request) async {
          requests++;
          expect(request.followRedirects, false);
          return http.Response(
            '',
            302,
            headers: {'location': 'https://external.com'},
          );
        }),
      )..token = 'secret';
      await expectLater(
        api.asset(Uri.parse('https://external.com/api/client/notes/1/asset')),
        throwsA(isA<ApiException>()),
      );
      await expectLater(
        api.asset(Uri.parse('https://example.com/login')),
        throwsA(isA<ApiException>()),
      );
      expect(requests, 0);
      await expectLater(
        api.asset(
          Uri.parse('https://example.com/api/client/notes/1/asset?path=a.png'),
        ),
        throwsA(isA<ApiException>()),
      );
      expect(requests, 1);
    },
  );
}
