import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import '../api.dart';
import '../app_state.dart';
import '../reader_document.dart';
import 'common.dart';

class ReaderView extends StatefulWidget {
  const ReaderView({
    super.key,
    required this.state,
    required this.source,
    required this.title,
    required this.category,
    this.summary = '',
    this.position = 0,
    this.fragment,
    required this.onLink,
    this.onProgress,
    this.onReady,
  });
  final AppState state;
  final String source, title, category, summary;
  final String? fragment;
  final double position;
  final Future<void> Function(String) onLink;
  final void Function(double)? onProgress;
  final void Function(WebViewController, List<OutlineEntry>)? onReady;
  @override
  State<ReaderView> createState() => _ReaderViewState();
}

class _ReaderViewState extends State<ReaderView> {
  WebViewController? _controller;
  String? _error;
  bool _loading = true;
  bool? _dark;
  double? _size;
  int _generation = 0;
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final dark = Theme.of(context).brightness == Brightness.dark;
    if (_controller == null && _generation == 0) {
      _dark = dark;
      _size = widget.state.fontSize;
      _load();
    } else if (_controller != null &&
        (_dark != dark || _size != widget.state.fontSize)) {
      _dark = dark;
      _size = widget.state.fontSize;
      _controller!.runJavaScript(
        'document.documentElement.classList.toggle("dark",$dark);document.body.style.fontSize="${widget.state.fontSize}px";',
      );
    }
  }

  @override
  void didUpdateWidget(ReaderView old) {
    super.didUpdateWidget(old);
    if (old.source != widget.source) _load();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final epoch = widget.state.sessionEpoch;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final css = await rootBundle.loadString('assets/reader.css');
      final document = await makeReaderDocument(
        source: widget.source,
        title: widget.title,
        category: widget.category,
        summary: widget.summary,
        server: widget.state.api.server,
        css: css,
        fontSize: widget.state.fontSize,
        dark: _dark ?? false,
        loadImage: (uri) async {
          final response = await widget.state.api.asset(uri);
          final mime =
              response.headers['content-type']?.split(';').first ?? 'image/png';
          if (!mime.startsWith('image/')) throw const ApiException('不是图片附件');
          return 'data:$mime;base64,${base64Encode(response.bodyBytes)}';
        },
      );
      if (!mounted ||
          generation != _generation ||
          epoch != widget.state.sessionEpoch) {
        return;
      }
      final controller = WebViewController();
      await controller.setJavaScriptMode(JavaScriptMode.unrestricted);
      await controller.setBackgroundColor(
        (_dark ?? false) ? const Color(0xff19171f) : Colors.white,
      );
      await controller.addJavaScriptChannel(
        'Wikitide',
        onMessageReceived: (message) async {
          if (!mounted || epoch != widget.state.sessionEpoch) return;
          try {
            final json = jsonDecode(message.message) as Map<String, dynamic>;
            switch (json['type']) {
              case 'progress':
                final value = json['value'];
                if (value is num && value.isFinite) {
                  widget.onProgress?.call(value.toDouble().clamp(0, 1));
                }
              case 'link':
                if (json['value'] is String) {
                  await widget.onLink(json['value'] as String);
                }
              case 'copy':
                if (json['value'] is String) {
                  await Clipboard.setData(
                    ClipboardData(text: json['value'] as String),
                  );
                  if (mounted) {
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(const SnackBar(content: Text('代码已复制')));
                  }
                }
            }
          } catch (error) {
            if (mounted) showError(context, error);
          }
        },
      );
      await controller.setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) => request.url == 'about:blank'
              ? NavigationDecision.navigate
              : NavigationDecision.prevent,
          onPageFinished: (_) async {
            if (!mounted || epoch != widget.state.sessionEpoch) return;
            widget.onReady?.call(controller, document.outline);
            final fragment = widget.fragment;
            if (fragment != null && fragment.isNotEmpty) {
              await controller.runJavaScript('jump(${jsonEncode(fragment)});');
            } else if (widget.position > 0) {
              await controller.runJavaScript(
                'window.scrollTo(0,(document.documentElement.scrollHeight-window.innerHeight)*${widget.position.clamp(0, 1)});',
              );
            }
            if (mounted) setState(() => _loading = false);
          },
          onWebResourceError: (error) {
            if (mounted && error.isForMainFrame == true) {
              setState(() {
                _error = '正文加载失败，请重试';
                _loading = false;
              });
            }
          },
        ),
      );
      if (!mounted || generation != _generation) {
        return;
      }
      setState(() => _controller = controller);
      await controller.loadHtmlString(document.html);
      if (document.imageFailures > 0 && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${document.imageFailures} 张图片未加载，刷新可以重试')),
        );
      }
    } catch (error) {
      if (mounted && generation == _generation) {
        setState(() {
          _error = error.toString();
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(24),
              child: Text(_error!, textAlign: TextAlign.center),
            ),
            FilledButton(onPressed: _load, child: const Text('重新加载')),
          ],
        ),
      );
    }
    return Stack(
      children: [
        if (_controller != null) WebViewWidget(controller: _controller!),
        if (_loading)
          const Positioned.fill(
            child: ColoredBox(
              color: Color(0x99ffffff),
              child: Center(child: CircularProgressIndicator()),
            ),
          ),
      ],
    );
  }
}
