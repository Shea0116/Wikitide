import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:wikitide/api.dart';
import 'package:wikitide/agent/agent_api.dart';

void main() {
  final body = {
    'question': '闭包',
    'history': [],
    'summary': {},
    'memory_context': [],
  };
  test('SSE parser handles split UTF-8 and multiline events', () async {
    final bytes = utf8.encode(
      'event: delta\ndata: {"text":\ndata: "中文"}\n\nevent: done\ndata: {"answer":"完成","sources":[]}\n\n',
    );
    final events = await decodeAgentEvents(
      Stream.fromIterable(bytes.map((v) => [v])),
    ).toList();
    expect(events.map((e) => e.type), ['delta', 'done']);
    expect(events.first.data['text'], '中文');
  });
  test(
    'native transport uses bearer only and requires explicit completion',
    () async {
      final api = WikiApi(server: 'https://example.test')
        ..token = 'native-only';
      var finished = false;
      final transient = AgentTransientApi(
        api,
        clientFactory: () => MockClient((request) async {
          expect(request.headers['Authorization'], 'Bearer native-only');
          expect(request.headers.containsKey('cookie'), false);
          expect(request.followRedirects, false);
          return http.Response(
            'event: delta\ndata: {"text":"片段"}\n\n${finished ? 'event: done\ndata: {"answer":"回答","sources":[]}\n\n' : ''}',
            200,
            headers: {'content-type': 'text/event-stream; charset=utf-8'},
          );
        }),
      );
      await expectLater(
        transient.ask(body).toList(),
        throwsA(isA<ApiException>()),
      );
      finished = true;
      expect((await transient.ask(body).toList()).last.type, 'done');
      api.close();
    },
  );
  test('role, memory count and byte limits fail before network', () {
    for (final invalid in [
      {
        ...body,
        'history': [
          {'role': 'system', 'content': 'bad'},
        ],
      },
      {
        ...body,
        'memory_context': List.filled(6, {'content': 'memory'}),
      },
      {...body, 'question': '中' * 30000},
    ]) {
      expect(
        () => AgentTransientApi.validateRequest(invalid),
        throwsA(isA<ApiException>()),
      );
    }
  });
}
