import 'dart:convert';
import 'package:html/dom.dart' as dom;
import 'package:html/parser.dart' as html;
import 'api.dart';

class OutlineEntry {
  const OutlineEntry(this.id, this.title, this.level);
  final String id, title;
  final int level;
}

class ReaderDocument {
  const ReaderDocument(this.html, this.outline, this.imageFailures);
  final String html;
  final List<OutlineEntry> outline;
  final int imageFailures;
}

Uri? assetUri(String href, String server) {
  final base = Uri.parse(server), target = base.resolve(href);
  if (target.origin != base.origin) return null;
  final match = RegExp(r'^/asset/(\d+)$').firstMatch(target.path);
  if (match != null) {
    return target.replace(path: '/api/client/notes/${match[1]}/asset');
  }
  if (RegExp(r'^/api/client/notes/\d+/asset$').hasMatch(target.path)) {
    return target;
  }
  return null;
}

Future<ReaderDocument> makeReaderDocument({
  required String source,
  required String title,
  required String category,
  required String summary,
  required String server,
  required String css,
  required double fontSize,
  required bool dark,
  required Future<String> Function(Uri) loadImage,
}) async {
  final fragment = html.parseFragment(source);
  for (final node in fragment.querySelectorAll(
    'script,iframe,object,embed,base,link,meta,form',
  )) {
    node.remove();
  }
  for (final node in fragment.querySelectorAll('*')) {
    node.attributes.removeWhere(
      (name, value) => name.toString().toLowerCase().startsWith('on'),
    );
  }
  final outline = <OutlineEntry>[];
  final ids = <String>{};
  for (final heading in fragment.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
    final slug = heading.text
        .toLowerCase()
        .replaceAll(RegExp(r'[^\p{L}\p{N}_ -]', unicode: true), '')
        .replaceAll(' ', '-');
    final base = heading.id.isNotEmpty
        ? heading.id
        : slug.isEmpty
        ? 'section'
        : slug;
    var id = base, duplicate = 0;
    while (ids.contains(id)) {
      id = '$base-${++duplicate}';
    }
    ids.add(id);
    heading.id = id;
    outline.add(
      OutlineEntry(
        id,
        heading.text,
        int.parse(heading.localName!.substring(1)),
      ),
    );
  }
  int failures = 0;
  // Images are authorized by the API and embedded as data. The WebView never
  // receives the session token, cookies, or access to remote subresources.
  final images = fragment.querySelectorAll('img');
  for (final image in images) {
    final uri = assetUri(image.attributes['src'] ?? '', server);
    try {
      if (uri == null) throw const ApiException('图片地址无效');
      image.attributes['src'] = await loadImage(uri);
      image.attributes.remove('srcset');
    } catch (error) {
      // Auth expiry is fatal; never display already retrieved private content.
      if (error is ApiException && error.status == 401) rethrow;
      failures++;
      image.replaceWith(
        dom.Element.tag('span')
          ..classes.add('image-error')
          ..text = '图片未加载：${image.attributes['alt'] ?? '附件'}',
      );
    }
  }
  for (final table in fragment.querySelectorAll('table')) {
    final wrapper = dom.Element.tag('div')..classes.add('table-scroll');
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  String escape(String value) => const HtmlEscape().convert(value);
  return ReaderDocument(
    '''<!doctype html><html class="${dark ? 'dark' : ''}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">
<style>$css body{font-size:${fontSize}px}</style></head><body><article>
<div class="category">${escape(category)}</div><h1>${escape(title)}</h1>
${summary.isEmpty ? '' : '<p class="summary">${escape(summary)}</p>'}
${fragment.outerHtml}</article><script>$readerScript</script></body></html>''',
    outline,
    failures,
  );
}

const readerScript = r'''
const send = (value) => { if(window.Wikitide) Wikitide.postMessage(JSON.stringify(value)); };
let scrollTimer;
window.addEventListener('scroll', () => {
  clearTimeout(scrollTimer);
  scrollTimer=setTimeout(() => {
    const span=document.documentElement.scrollHeight-window.innerHeight;
    send({type:'progress',value:span>0?Math.min(1,Math.max(0,window.scrollY/span)):1});
  },700);
});
function jump(id){
  let heading=document.getElementById(id)||document.getElementById(id.toLowerCase());
  if(!heading) heading=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(h=>h.textContent===id);
  if(heading) heading.scrollIntoView();
}
document.addEventListener('click', event => {
  const a=event.target.closest('a'); if(!a)return;
  event.preventDefault(); const href=a.getAttribute('href')||'';
  if(href.startsWith('#')){try{jump(decodeURIComponent(href.slice(1)));}catch(_){jump(href.slice(1));}}
  else send({type:'link',value:href});
});
document.querySelectorAll('pre').forEach(pre => {
  const code=pre.querySelector('code');const button=document.createElement('button');
  button.type='button';button.textContent='复制代码';button.setAttribute('aria-label','复制代码');
  button.onclick=()=>send({type:'copy',value:code?code.textContent:pre.textContent.replace('复制代码','')});
  pre.appendChild(button);
});
''';
