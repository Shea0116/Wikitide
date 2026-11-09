import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:wikitide/offline_library.dart';

void main() {
  late Directory root;
  setUp(() async {
    root = await Directory.systemTemp.createTemp('wikitide-offline-test-');
  });
  tearDown(() async {
    await root.delete(recursive: true);
  });
  OfflineLibrary library({bool fail = false}) => OfflineLibrary(
    Uri.parse('https://example.test'),
    rootOverride: root,
    clientFactory: () => MockClient((request) async {
      expect(request.url.host, 'example.test');
      expect(request.headers.containsKey('cookie'), false);
      if (fail) return http.Response('not public', 404);
      final id = int.parse(request.url.pathSegments.last);
      return http.Response(
        jsonEncode({
          'id': id,
          'title': 'Public article $id',
          'html': '<h2>Public</h2>',
          'sha': 'version-$id',
          'downloaded_at': id,
          'missing': [],
        }),
        200,
      );
    }),
  );
  test(
    'saves and lists complete articles atomically, with concurrent writes serialized',
    () async {
      final store = library();
      await Future.wait([store.save(1), store.save(2), store.save(1)]);
      final entries = await store.entries();
      expect(entries.map((item) => item['id']), [2, 1]);
      expect(entries.every((item) => (item['bytes'] as int) > 0), true);
      await store.remove(1);
      expect((await store.entries()).length, 1);
    },
  );
  test(
    'failed download preserves an existing copy and ignores corrupt files',
    () async {
      await library().save(1);
      await expectLater(library(fail: true).save(1), throwsStateError);
      await File('${root.path}/bad.json').writeAsString('{broken');
      await File(
        '${root.path}/wrong.json',
      ).writeAsString('{"id":"not-an-integer"}');
      final rows = await library().entries();
      expect(rows.length, 1);
      expect(rows.single['sha'], 'version-1');
    },
  );
}
