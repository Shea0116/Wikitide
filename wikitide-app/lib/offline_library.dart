import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:webview_flutter/webview_flutter.dart';

class OfflineLibrary {
  OfflineLibrary(
    this.origin, {
    this.rootOverride,
    this.clientFactory = http.Client.new,
  });
  final Uri origin;
  final Directory? rootOverride;
  final http.Client Function() clientFactory;
  static Future<void> _writes = Future<void>.value();
  static const limit = 50 * 1024 * 1024;
  Future<Directory> directory() async {
    if (rootOverride != null) return rootOverride!.create(recursive: true);
    final root = await getApplicationSupportDirectory();
    final key = base64Url
        .encode(utf8.encode(origin.origin))
        .replaceAll('=', '');
    return Directory('${root.path}/offline/$key').create(recursive: true);
  }

  Future<List<Map<String, dynamic>>> entries() async {
    final result = <Map<String, dynamic>>[];
    await for (final file in (await directory()).list()) {
      if (file is! File || !file.path.endsWith('.json')) continue;
      try {
        if (await file.length() > 12 * 1024 * 1024) continue;
        final data =
            jsonDecode(await file.readAsString()) as Map<String, dynamic>;
        if (data['id'] is! int ||
            (data['id'] as int) <= 0 ||
            data['title'] is! String ||
            data['downloaded_at'] is! int ||
            data['html'] is! String ||
            data['sha'] is! String) {
          continue;
        }
        data['bytes'] = await file.length();
        result.add(data);
      } catch (_) {
        /* A corrupt cache must not prevent other articles opening. */
      }
    }
    result.sort(
      (a, b) =>
          (b['downloaded_at'] as int).compareTo(a['downloaded_at'] as int),
    );
    return result;
  }

  Future<Map<String, dynamic>> request(String path) async {
    final client = clientFactory();
    try {
      final response = await client
          .send(
            http.Request('GET', origin.resolve(path))..followRedirects = false,
          )
          .timeout(const Duration(seconds: 20));
      if (response.statusCode != 200) {
        throw StateError(
          response.statusCode == 404 ? '此文章不在公开离线范围' : '下载失败，请稍后重试',
        );
      }
      final bytes = <int>[];
      await for (final chunk in response.stream.timeout(
        const Duration(seconds: 20),
      )) {
        if (bytes.length + chunk.length > 12 * 1024 * 1024) {
          throw StateError('离线包过大');
        }
        bytes.addAll(chunk);
      }
      return jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> save(int id) async {
    final task = _writes.then((_) => _save(id));
    _writes = task.then<void>((_) {}, onError: (Object _) {});
    return task;
  }

  Future<Map<String, dynamic>> _save(int id) async {
    final data = await request('/api/offline/$id');
    if (data['id'] != id || data['html'] is! String || data['sha'] is! String) {
      throw StateError('离线数据无效');
    }
    final body = jsonEncode(data);
    final current = await entries();
    final size =
        current
            .where((item) => item['id'] != id)
            .fold<int>(0, (sum, item) => sum + (item['bytes'] as int)) +
        utf8.encode(body).length;
    if (size > limit ||
        current.where((item) => item['id'] != id).length >= 200) {
      throw StateError('离线空间已满（50 MB / 200 篇），请先移除部分副本');
    }
    final root = await directory();
    final temporary = File('${root.path}/$id.tmp');
    await temporary.writeAsString(body, flush: true);
    await temporary.rename('${root.path}/$id.json');
    return data;
  }

  Future<void> remove(int id) async {
    final task = _writes.then((_) => _remove(id));
    _writes = task.then<void>((_) {}, onError: (Object _) {});
    return task;
  }

  Future<void> _remove(int id) async {
    final file = File('${(await directory()).path}/$id.json');
    if (await file.exists()) await file.delete();
  }
}

class OfflineLibraryPage extends StatefulWidget {
  const OfflineLibraryPage({super.key, required this.library});
  final OfflineLibrary library;
  @override
  State<OfflineLibraryPage> createState() => _OfflineLibraryPageState();
}

class _OfflineLibraryPageState extends State<OfflineLibraryPage> {
  List<Map<String, dynamic>> rows = [];
  bool busy = true;
  String? message;
  @override
  void initState() {
    super.initState();
    reload();
  }

  Future<void> reload() async {
    try {
      final values = await widget.library.entries();
      if (mounted) {
        setState(() {
          rows = values;
          busy = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          busy = false;
          message = '无法读取离线目录';
        });
      }
    }
  }

  Future<void> update(Map<String, dynamic> item) async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      final status = await widget.library.request(
        '/api/offline/${item['id']}/status',
      );
      if (status['sha'] == item['sha']) {
        if (mounted) setState(() => message = '这篇文章已是最新副本');
      } else {
        await widget.library.save(item['id'] as int);
        if (mounted) setState(() => message = '已下载最新公开版本');
      }
    } catch (error) {
      if (mounted) setState(() => message = '检查失败，原离线副本仍保留：$error');
    }
    await reload();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('离线阅读')),
    body: Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            '公开文章副本 · ${(rows.fold<int>(0, (sum, item) => sum + (item['bytes'] as int)) / 1048576).toStringAsFixed(1)} / 50 MB\n离线副本不会自动更新；联网后可检查最新版本。',
          ),
        ),
        if (message != null)
          Padding(padding: const EdgeInsets.all(12), child: Text(message!)),
        if (busy) const LinearProgressIndicator(),
        Expanded(
          child: rows.isEmpty
              ? const Center(child: Text('打开公开文章，点击“保存离线”即可下载。'))
              : ListView.builder(
                  itemCount: rows.length,
                  itemBuilder: (context, index) {
                    final item = rows[index];
                    return ListTile(
                      title: Text(item['title'] as String),
                      subtitle: Text(
                        '保存于 ${DateTime.fromMillisecondsSinceEpoch((item['downloaded_at'] as int) * 1000).toLocal()}',
                      ),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => OfflineReaderPage(
                            item: item,
                            origin: widget.library.origin,
                          ),
                        ),
                      ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconButton(
                            tooltip: '检查并更新',
                            onPressed: busy ? null : () => update(item),
                            icon: const Icon(Icons.refresh),
                          ),
                          IconButton(
                            tooltip: '移除离线副本',
                            onPressed: busy
                                ? null
                                : () async {
                                    await widget.library.remove(
                                      item['id'] as int,
                                    );
                                    await reload();
                                  },
                            icon: const Icon(Icons.delete_outline),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
      ],
    ),
  );
}

class OfflineReaderPage extends StatefulWidget {
  const OfflineReaderPage({
    super.key,
    required this.item,
    required this.origin,
  });
  final Map<String, dynamic> item;
  final Uri origin;
  @override
  State<OfflineReaderPage> createState() => _OfflineReaderPageState();
}

class _OfflineReaderPageState extends State<OfflineReaderPage> {
  final controller = WebViewController();
  bool ready = false;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    final css = await rootBundle.loadString('assets/reader.css');
    final title = const HtmlEscape().convert(widget.item['title'] as String);
    await controller.setJavaScriptMode(JavaScriptMode.disabled);
    await controller.setNavigationDelegate(
      NavigationDelegate(
        onNavigationRequest: (request) =>
            request.url == 'about:blank' ||
                request.url.startsWith('about:blank#') ||
                request.url.startsWith('data:text/html')
            ? NavigationDecision.navigate
            : NavigationDecision.prevent,
      ),
    );
    await controller.loadHtmlString(
      '<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><style>$css body{padding:20px;max-width:850px;margin:auto}img{max-width:100%}pre{overflow:auto}</style></head><body><h1>$title</h1><p>离线副本 · 保存于 ${DateTime.fromMillisecondsSinceEpoch((widget.item['downloaded_at'] as int) * 1000).toLocal()}</p><article class="prose">${widget.item['html']}</article></body></html>',
    );
    if (mounted) setState(() => ready = true);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(widget.item['title'] as String)),
    body: ready
        ? WebViewWidget(controller: controller)
        : const Center(child: CircularProgressIndicator()),
  );
}
