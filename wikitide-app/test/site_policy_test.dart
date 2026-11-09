import 'package:flutter_test/flutter_test.dart';
import 'package:wikitide/web_client.dart';

void main() {
  final policy = SitePolicy('https://wikitide.soulshea.vip');
  test('all real website pages retain the same origin and shared UI', () {
    for (final path in [
      '/',
      '/browse',
      '/graph',
      '/n/58',
      '/n/58/edit',
      '/login',
      '/admin',
      '/search?q=Flutter',
    ]) {
      expect(policy.contains(policy.home.resolve(path)), true);
    }
  });
  test(
    'lookalike domains and insecure replacements cannot remain inside the app',
    () {
      for (final url in [
        'https://wikitide.soulshea.vip.evil.com',
        'http://wikitide.soulshea.vip',
        'https://wikitide.soulshea.vip:8443',
        'file:///tmp/private',
      ]) {
        expect(policy.contains(Uri.parse(url)), false);
      }
    },
  );
  test('unsafe link schemes are not launched externally', () {
    for (final scheme in [
      'javascript:alert(1)',
      'data:text/html,test',
      'file:///etc/passwd',
      'intent://test',
    ]) {
      expect(policy.canOpenExternally(Uri.parse(scheme)), false);
    }
    expect(policy.canOpenExternally(Uri.parse('https://example.com')), true);
  });
}
