import 'package:flutter_test/flutter_test.dart';
import 'package:wikitide/api.dart';
import 'package:wikitide/reader_document.dart';

void main() {
  test(
    'reader preserves colors, rowspan, list numbering and creates unique anchors',
    () async {
      final document = await makeReaderDocument(
        source:
            '<h2><span style="color:#dc143c">面试问题</span></h2><h2>面试问题</h2><ol start="3"><li>第三项</li></ol><table><tr><td rowspan="2">合并</td></tr></table><pre><code>print(1)</code></pre>',
        title: '<script>title</script>',
        category: 'Frontend',
        summary: '',
        server: 'https://example.com',
        css: '',
        fontSize: 18,
        dark: true,
        loadImage: (_) async => '',
      );
      expect(document.html, contains('color:#dc143c'));
      expect(document.html, contains('rowspan="2"'));
      expect(document.html, contains('start="3"'));
      expect(document.html, contains('class="table-scroll"'));
      expect(document.html, contains('&lt;script&gt;title&lt;&#47;script&gt;'));
      expect(document.outline.map((e) => e.id), ['面试问题', '面试问题-1']);
      expect(document.html, contains("default-src 'none'"));
    },
  );

  test(
    'images are embedded with scoped URLs and failed images are explicit',
    () async {
      final requests = <Uri>[];
      final document = await makeReaderDocument(
        source:
            '<img src="/asset/4?path=static%2Fx.png" alt="内图"><img src="https://evil.com/x.png"><script>evil()</script><p onclick="evil()">safe</p>',
        title: 'Title',
        category: '',
        summary: '',
        server: 'https://example.com',
        css: '',
        fontSize: 17,
        dark: false,
        loadImage: (uri) async {
          requests.add(uri);
          return 'data:image/png;base64,eA==';
        },
      );
      expect(requests.single.path, '/api/client/notes/4/asset');
      expect(requests.single.queryParameters['path'], 'static/x.png');
      expect(document.html, contains('data:image/png;base64,eA=='));
      expect(document.html, isNot(contains('onclick="evil()"')));
      expect(document.html, isNot(contains('evil()</script>')));
      expect(document.html, isNot(contains('evil.com')));
      expect(document.imageFailures, 1);
    },
  );

  test(
    'expiry aborts a private reader rather than showing partially fetched content',
    () async {
      await expectLater(
        makeReaderDocument(
          source: '<img src="/asset/1?path=a.png">',
          title: 'Private',
          category: '',
          summary: '',
          server: 'https://example.com',
          css: '',
          fontSize: 17,
          dark: false,
          loadImage: (_) async =>
              throw const ApiException('expired', status: 401),
        ),
        throwsA(isA<ApiException>()),
      );
    },
  );
}
