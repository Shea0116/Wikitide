import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'api.dart';
import 'article_find.dart';
import 'desktop_shell.dart';
import 'offline_library.dart';
import 'agent/agent_session.dart';
import 'agent/memory_page.dart';

/// The website owns all content. macOS adds desktop navigation around it;
/// mobile keeps the website's complete responsive interface.
class SitePolicy {
  SitePolicy(String server) : home = Uri.parse(normalizeServer(server));
  final Uri home;
  bool contains(Uri uri) =>
      {'https', 'http'}.contains(uri.scheme) &&
      uri.hasAuthority &&
      uri.userInfo.isEmpty &&
      uri.origin == home.origin;
  bool canOpenExternally(Uri uri) =>
      {'https', 'http', 'mailto'}.contains(uri.scheme);
}

void runWebClient() {
  WidgetsFlutterBinding.ensureInitialized();
  final policy = SitePolicy(
    const String.fromEnvironment(
      'WIKITIDE_SERVER',
      defaultValue: defaultServer,
    ),
  );
  runApp(
    MaterialApp(
      title: '识汐 WikiTide',
      debugShowCheckedModeBanner: false,
      // From the current site's hex.css --wash and --ink. These only appear
      // before loading or if the connection fails; page styling stays untouched.
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff28251f)),
        scaffoldBackgroundColor: const Color(0xfff1ebdf),
      ),
      home: WebClientPage(policy: policy),
    ),
  );
}

class WebClientPage extends StatefulWidget {
  const WebClientPage({super.key, required this.policy});
  final SitePolicy policy;
  @override
  State<WebClientPage> createState() => _WebClientPageState();
}

class _WebClientPageState extends State<WebClientPage> {
  late final WebViewController _controller;
  static const MethodChannel _desktopChannel = MethodChannel(
    'wikitide/desktop',
  );
  final FocusNode _searchFocusNode = FocusNode();
  final FocusNode _findFocusNode = FocusNode();
  final TextEditingController _findController = TextEditingController();
  Timer? _findDebounce;
  Future<void> _findWork = Future<void>.value();
  int _findRevision = 0;
  int _findDocument = 0;
  bool _canFindArticle = false;
  bool _findOpen = false;
  bool _findBusy = false;
  String? _findError;
  int _findTotal = 0;
  int _findCurrent = 0;
  int _progress = 0;
  String? _error;
  bool _canGoBack = false;
  bool _canGoForward = false;
  bool _exiting = false;
  bool _desktopReady = false;
  bool _manualSidebarCollapsed = false;
  bool? _articleSidebarExpanded;
  bool _accountExpanded = false;
  bool _dark = false;
  bool _isAdmin = false;
  String? _accountName;
  String _pageTitle = '首页';
  late Uri _currentUri = widget.policy.home;
  SharedPreferences? _preferences;
  bool _savingOffline = false;

  Widget _offlineTools() => Row(
    mainAxisAlignment: MainAxisAlignment.end,
    children: [
      TextButton.icon(
        onPressed: _openNativeMemory,
        icon: const Icon(Icons.memory_outlined, size: 18),
        label: const Text('本机记忆'),
      ),
      if (_isReadingArticle)
        TextButton.icon(
          onPressed: _savingOffline ? null : _saveOffline,
          icon: const Icon(Icons.download_outlined, size: 18),
          label: Text(_savingOffline ? '正在保存…' : '保存离线'),
        ),
      TextButton.icon(
        onPressed: () => Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) =>
                OfflineLibraryPage(library: OfflineLibrary(widget.policy.home)),
          ),
        ),
        icon: const Icon(Icons.offline_pin_outlined, size: 18),
        label: const Text('离线阅读'),
      ),
    ],
  );

  Future<void> _openNativeMemory() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NativeMemoryPage(origin: widget.policy.home.origin),
      ),
    );
  }

  Future<void> _saveOffline() async {
    final id = int.tryParse(
      _currentUri.pathSegments.where((part) => part.isNotEmpty).last,
    );
    if (id == null) return;
    setState(() => _savingOffline = true);
    try {
      final data = await OfflineLibrary(widget.policy.home).save(id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              (data['missing'] as List).isEmpty
                  ? '已保存文章与图片，可离线阅读'
                  : '已保存正文；部分外部或过大的图片未包含',
            ),
          ),
        );
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('保存失败：$error')));
      }
    } finally {
      if (mounted) setState(() => _savingOffline = false);
    }
  }

  bool get _desktop => defaultTargetPlatform == TargetPlatform.macOS;
  bool get _isReadingArticle =>
      RegExp(r'^/n/\d+/?$').hasMatch(_currentUri.path);
  bool get _sidebarCollapsed => _isReadingArticle
      ? !(_articleSidebarExpanded ?? false)
      : _manualSidebarCollapsed;

  void _toggleSidebar() {
    setState(() {
      if (_isReadingArticle) {
        _articleSidebarExpanded = _sidebarCollapsed;
      } else {
        _manualSidebarCollapsed = !_manualSidebarCollapsed;
      }
    });
    _preferences?.setBool('sidebar-collapsed', _manualSidebarCollapsed);
  }

  @override
  void dispose() {
    if (_desktop) _desktopChannel.setMethodCallHandler(null);
    _searchFocusNode.dispose();
    _findDebounce?.cancel();
    _findFocusNode.dispose();
    _findController.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    if (_desktop) _desktopChannel.setMethodCallHandler(_handleDesktopCommand);
    _controller = WebViewController();
    _initialize();
  }

  Future<dynamic> _handleDesktopCommand(MethodCall call) async {
    if (!mounted) return;
    switch (call.method) {
      case 'prepareForUpdate':
        if (!NativeAgentReadiness.canRestart) return false;
        if (!_desktopReady || _error != null) return false;
        try {
          final ready = await _controller.runJavaScriptReturningResult(
            updateReadinessScript,
          );
          return ready == true || ready.toString() == 'true';
        } catch (_) {
          return false;
        }
      case 'toggleSidebar':
        _toggleSidebar();
        return;
      case 'focusSearch':
        if (_searchFocusNode.context != null) {
          _searchFocusNode.requestFocus();
        } else {
          await _navigate('/search');
        }
        return;
      case 'findInArticle':
        _openFind();
        return;
      case 'findNext':
        _moveFind(1);
        return;
      case 'findPrevious':
        _moveFind(-1);
        return;
      default:
        throw MissingPluginException('Unknown desktop command: ${call.method}');
    }
  }

  Future<void> _initialize() async {
    try {
      _preferences = await SharedPreferences.getInstance();
      _manualSidebarCollapsed =
          _preferences?.getBool('sidebar-collapsed') ?? false;
      await _controller.setJavaScriptMode(JavaScriptMode.unrestricted);
      await _controller.addJavaScriptChannel(
        'WikiTideFind',
        onMessageReceived: (message) {
          if (message.message == 'close') _closeFind();
        },
      );
      // WKWebView's macOS adapter does not implement backgroundColor.
      if (!_desktop) {
        await _controller.setBackgroundColor(const Color(0xfff1ebdf));
      }
      await _controller.addJavaScriptChannel(
        'WikiTideDownload',
        onMessageReceived: (message) async {
          try {
            final data = jsonDecode(message.message) as Map<String, dynamic>;
            final bytes = base64Decode(data['data'] as String);
            if (bytes.length > 8 * 1024 * 1024) {
              throw const FormatException('草稿过大');
            }
            await const MethodChannel(
              'wikitide/attachments',
            ).invokeMethod<bool>('save', {
              'bytes': bytes,
              'name': data['name'] as String,
            });
          } catch (_) {
            if (mounted) {
              ScaffoldMessenger.of(
                context,
              ).showSnackBar(const SnackBar(content: Text('草稿下载失败，请重试。')));
            }
          }
        },
      );
      if (_controller.platform is WebKitWebViewController) {
        await (_controller.platform as WebKitWebViewController)
            .setAllowsBackForwardNavigationGestures(true);
      }
      await _controller.setNavigationDelegate(
        NavigationDelegate(
          onProgress: (progress) {
            if (mounted) setState(() => _progress = progress);
          },
          onPageStarted: (url) {
            if (mounted) {
              _findDebounce?.cancel();
              _findRevision++;
              _findDocument++;
              _findFocusNode.unfocus();
              setState(() {
                _findOpen = false;
                _canFindArticle = false;
                _findBusy = false;
                _findError = null;
                _findTotal = 0;
                _findCurrent = 0;
                final nextUri = Uri.tryParse(url) ?? widget.policy.home;
                if (nextUri.path != _currentUri.path) {
                  _articleSidebarExpanded = null;
                }
                _error = null;
                _progress = 0;
                _desktopReady = false;
                _currentUri = nextUri;
              });
            }
          },
          onPageFinished: (_) async {
            await _controller.runJavaScript(draftDownloadBridge);
            if (_desktop) {
              await _controller.runJavaScript(desktopPageStyle);
            }
            await _controller.runJavaScript(articleFindScript);
            await _syncDesktopInfo();
            if (_isReadingArticle && widget.policy.contains(_currentUri)) {
              await _preferences?.setString(
                'last-article:${widget.policy.home.origin}',
                _currentUri.replace(query: 'resume=1').toString(),
              );
            }
            final back = await _controller.canGoBack();
            final forward = await _controller.canGoForward();
            if (mounted) {
              setState(() {
                _canGoBack = back;
                _canGoForward = forward;
                _progress = 100;
                _desktopReady = true;
              });
            }
          },
          onNavigationRequest: (request) {
            if (!request.isMainFrame || request.url == 'about:blank') {
              return NavigationDecision.navigate;
            }
            final uri = Uri.tryParse(request.url);
            if (uri != null && widget.policy.contains(uri)) {
              return NavigationDecision.navigate;
            }
            if (uri != null && widget.policy.canOpenExternally(uri)) {
              _external(uri);
            }
            return NavigationDecision.prevent;
          },
          onWebResourceError: (error) {
            if (mounted && error.isForMainFrame == true) {
              setState(() {
                _error = '无法连接 WikiTide，请检查网络后重试。';
                _progress = 100;
                _canFindArticle = false;
                _findOpen = false;
              });
            }
          },
        ),
      );
      final saved = Uri.tryParse(
        _preferences?.getString('last-article:${widget.policy.home.origin}') ??
            '',
      );
      await _controller.loadRequest(
        saved != null &&
                widget.policy.contains(saved) &&
                RegExp(r'^/n/\d+/?$').hasMatch(saved.path)
            ? saved
            : widget.policy.home,
      );
    } catch (error) {
      if (mounted) setState(() => _error = '页面加载失败，请重试。');
    }
  }

  Future<void> _external(Uri uri) async {
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法打开链接')));
    }
  }

  Future<void> _checkForUpdates() async {
    try {
      await _desktopChannel.invokeMethod<void>('checkForUpdates');
    } on PlatformException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error.message ?? '暂时无法检查更新，请稍后重试。')),
        );
      }
    }
  }

  Future<void> _back() async {
    if (await _controller.canGoBack()) {
      await _controller.goBack();
      final back = await _controller.canGoBack();
      if (mounted) setState(() => _canGoBack = back);
    } else if (defaultTargetPlatform == TargetPlatform.android) {
      if (mounted) setState(() => _exiting = true);
      await SystemNavigator.pop();
    }
  }

  Future<void> _forward() async {
    if (await _controller.canGoForward()) await _controller.goForward();
  }

  Future<void> _navigate(String path) async {
    if (_accountExpanded) setState(() => _accountExpanded = false);
    await _controller.loadRequest(widget.policy.home.resolve(path));
  }

  Future<void> _search(String query) async {
    final trimmed = query.trim();
    if (trimmed.isEmpty) return;
    _searchFocusNode.unfocus();
    await _navigate('/search?q=${Uri.encodeQueryComponent(trimmed)}');
  }

  void _openFind() {
    if (!_canFindArticle || !_desktopReady || _error != null) return;
    setState(() => _findOpen = true);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_findOpen) return;
      _findFocusNode.requestFocus();
      _findController.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _findController.text.length,
      );
    });
    _findDebounce?.cancel();
    _runFind('search', _findController.text);
  }

  void _changeFind(String query) {
    _findDebounce?.cancel();
    _findRevision++;
    setState(() {
      _findBusy = true;
      _findError = null;
      _findTotal = 0;
      _findCurrent = 0;
    });
    _findDebounce = Timer(const Duration(milliseconds: 180), () {
      _runFind('search', query);
    });
  }

  void _moveFind(int direction) {
    if (!_findOpen || _findBusy || _findTotal == 0) return;
    _runFind('move', direction);
  }

  void _runFind(String action, Object argument) {
    final revision = ++_findRevision;
    final document = _findDocument;
    setState(() {
      _findBusy = true;
      _findError = null;
    });
    // Serialize WebView calls, and discard work invalidated by typing,
    // closing the bar, or navigating to a different document.
    _findWork = _findWork.then((_) async {
      if (!mounted || !_findOpen || revision != _findRevision) return;
      try {
        final result = await _controller.runJavaScriptReturningResult(
          'JSON.stringify(window.wikiTideArticleFind.$action(${jsonEncode(argument)}))',
        );
        var status = jsonDecode(result.toString());
        if (status is String) status = jsonDecode(status);
        if (!mounted ||
            revision != _findRevision ||
            document != _findDocument) {
          return;
        }
        setState(() {
          _findTotal = (status['total'] as num).toInt();
          _findCurrent = (status['current'] as num).toInt();
          _findBusy = false;
          _findError = status['available'] == true ? null : '当前页面没有可查找的正文';
        });
      } catch (_) {
        if (mounted && revision == _findRevision) {
          setState(() {
            _findBusy = false;
            _findTotal = 0;
            _findCurrent = 0;
            _findError = '查找失败，请重试';
          });
        }
      }
    });
  }

  void _closeFind() {
    if (!_findOpen) return;
    _findDebounce?.cancel();
    _findRevision++;
    final document = _findDocument;
    _findFocusNode.unfocus();
    setState(() {
      _findOpen = false;
      _findBusy = false;
      _findTotal = 0;
      _findCurrent = 0;
      _findError = null;
    });
    _findWork = _findWork.then((_) async {
      if (!mounted || document != _findDocument) return;
      try {
        await _controller.runJavaScript('window.wikiTideArticleFind?.clear()');
      } catch (_) {
        // Navigation can dispose the old document before cleanup runs.
      }
    });
  }

  Future<void> _toggleTheme() async {
    await _controller.runJavaScript(
      "document.querySelector('.theme-toggle')?.click()",
    );
    await _syncDesktopInfo();
  }

  Future<void> _logout() async {
    if (_accountExpanded) setState(() => _accountExpanded = false);
    await _controller.runJavaScript(
      "document.querySelector('form[data-logout]')?.requestSubmit()",
    );
  }

  Future<void> _syncDesktopInfo() async {
    try {
      final result = await _controller.runJavaScriptReturningResult(r'''
JSON.stringify(window.wikiTideNative?.version === 1 ? window.wikiTideNative.state() : {
  title: document.querySelector('main h1')?.textContent?.trim() || document.title,
  account: document.querySelector('.account-username')?.textContent?.trim() || null,
  admin: !!document.querySelector('.account-dropdown a[href="/admin"]'),
  dark: document.documentElement.dataset.theme === 'dark',
  article: !!document.querySelector('article.article .prose'),
  url: location.href
})
''');
      var metadata = jsonDecode(result.toString());
      if (metadata is String) metadata = jsonDecode(metadata);
      if (!mounted || metadata is! Map<String, dynamic>) return;
      setState(() {
        _currentUri =
            Uri.tryParse(metadata['url'] as String? ?? '') ?? _currentUri;
        _pageTitle = _currentUri.path == '/'
            ? '首页'
            : (metadata['title'] as String?)?.trim() ?? '识汐 WikiTide';
        if (_accountName != metadata['account']) _accountExpanded = false;
        _accountName = metadata['account'] as String?;
        _isAdmin = metadata['admin'] == true;
        _dark = metadata['dark'] == true;
        _canFindArticle = metadata['article'] == true;
      });
    } catch (_) {
      // Site content remains available even if a future website version
      // changes the optional account/title selectors.
    }
  }

  Future<void> _retry() async {
    setState(() {
      _error = null;
      _progress = 0;
    });
    await _controller.loadRequest(
      widget.policy.contains(_currentUri) ? _currentUri : widget.policy.home,
    );
  }

  Widget _webContent() => Stack(
    children: [
      WebViewWidget(controller: _controller),
      if (_desktop && !_desktopReady && _error == null)
        const Positioned.fill(child: ColoredBox(color: Color(0xfff1ebdf))),
      if (!_desktop && _progress < 100 && _error == null)
        const Positioned(
          left: 0,
          top: 0,
          right: 0,
          child: LinearProgressIndicator(
            minHeight: 2,
            color: Color(0xffa67c32),
            backgroundColor: Colors.transparent,
          ),
        ),
      if (_error != null)
        Positioned.fill(
          child: ColoredBox(
            color: const Color(0xfff1ebdf),
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    _error!,
                    style: const TextStyle(color: Color(0xff28251f)),
                  ),
                  const SizedBox(height: 24),
                  OutlinedButton(onPressed: _retry, child: const Text('重试')),
                ],
              ),
            ),
          ),
        ),
    ],
  );

  @override
  Widget build(BuildContext context) => CallbackShortcuts(
    bindings: {
      const SingleActivator(LogicalKeyboardKey.bracketLeft, meta: true): _back,
      const SingleActivator(LogicalKeyboardKey.bracketRight, meta: true):
          _forward,
      const SingleActivator(LogicalKeyboardKey.arrowLeft, alt: true): _back,
      const SingleActivator(LogicalKeyboardKey.arrowRight, alt: true): _forward,
      const SingleActivator(LogicalKeyboardKey.keyR, meta: true): () =>
          _controller.reload(),
      const SingleActivator(LogicalKeyboardKey.f5): () => _controller.reload(),
      if (_desktop) ...{
        const SingleActivator(LogicalKeyboardKey.keyF, meta: true): _openFind,
        const SingleActivator(LogicalKeyboardKey.keyG, meta: true): () =>
            _moveFind(1),
        const SingleActivator(
          LogicalKeyboardKey.keyG,
          meta: true,
          shift: true,
        ): () =>
            _moveFind(-1),
        if (_findOpen)
          const SingleActivator(LogicalKeyboardKey.escape): _closeFind,
      },
    },
    child: Focus(
      child: PopScope(
        canPop: _exiting || !_canGoBack,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop) _back();
        },
        child: Scaffold(
          body: SafeArea(
            child: _desktop
                ? DesktopShell(
                    content: Column(
                      children: [
                        _offlineTools(),
                        Expanded(child: _webContent()),
                      ],
                    ),
                    currentUri: _currentUri,
                    pageTitle: _pageTitle,
                    loading: _progress < 100 && _error == null,
                    canGoBack: _canGoBack,
                    canGoForward: _canGoForward,
                    dark: _dark,
                    sidebarCollapsed: _sidebarCollapsed,
                    accountName: _accountName,
                    isAdmin: _isAdmin,
                    accountExpanded: _accountExpanded,
                    searchFocusNode: _searchFocusNode,
                    canFindArticle:
                        _canFindArticle && _desktopReady && _error == null,
                    findOpen: _findOpen,
                    findBusy: _findBusy,
                    findError: _findError,
                    findTotal: _findTotal,
                    findCurrent: _findCurrent,
                    findController: _findController,
                    findFocusNode: _findFocusNode,
                    onOpenFind: _openFind,
                    onCloseFind: _closeFind,
                    onFindChanged: _changeFind,
                    onFindNext: () => _moveFind(1),
                    onFindPrevious: () => _moveFind(-1),
                    onNavigate: _navigate,
                    onSearch: _search,
                    onBack: _back,
                    onForward: _forward,
                    onReload: _controller.reload,
                    onCheckForUpdates:
                        const bool.fromEnvironment('WIKITIDE_APP_STORE')
                        ? null
                        : _checkForUpdates,
                    onToggleSidebar: _toggleSidebar,
                    onToggleTheme: _toggleTheme,
                    onOpenBrowser: () => _external(_currentUri),
                    onLogout: _logout,
                    onToggleAccount: () =>
                        setState(() => _accountExpanded = !_accountExpanded),
                    onOpenNativeMemory: _openNativeMemory,
                  )
                : Column(
                    children: [
                      _offlineTools(),
                      if (_canFindArticle && !_findOpen)
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton.icon(
                            onPressed: _openFind,
                            icon: const Icon(Icons.search, size: 18),
                            label: const Text('文内查找'),
                          ),
                        ),
                      if (_findOpen)
                        ArticleFindBar(
                          dark: _dark,
                          controller: _findController,
                          focusNode: _findFocusNode,
                          busy: _findBusy,
                          error: _findError,
                          total: _findTotal,
                          current: _findCurrent,
                          onChanged: _changeFind,
                          onNext: () => _moveFind(1),
                          onPrevious: () => _moveFind(-1),
                          onClose: _closeFind,
                        ),
                      Expanded(child: _webContent()),
                    ],
                  ),
          ),
        ),
      ),
    ),
  );
}

// Restart only after website-owned drafts have been saved/submitted.
const updateReadinessScript = r'''
(() => {
  if (window.wikiTideNative?.version === 1) return window.wikiTideNative.state().canRestart === true;
  const writing = document.getElementById('writing-save-status');
  if (writing && /未保存|生成中|保存中/.test(writing.textContent || '')) return false;
  const editor = document.querySelector('[data-draft-status]');
  if (editor && !['未修改', '已保存'].includes((editor.textContent || '').trim())) return false;
  return true;
})()
''';

// Only handles the website's existing blob download links. No styles, text,
// page navigation, forms, credentials or content rendering are changed.
const draftDownloadBridge = r'''
if (!window.__wikiTideDownloadInstalled) {
  window.__wikiTideDownloadInstalled = true;
  document.addEventListener('click', function(event) {
    const a = event.target.closest('a[download]');
    if (!a || !a.href.startsWith('blob:')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    fetch(a.href).then(response => response.blob()).then(blob => {
      const reader = new FileReader();
      reader.onload = () => WikiTideDownload.postMessage(JSON.stringify({name:a.download || '草稿.md', data:reader.result.split(',')[1]}));
      reader.readAsDataURL(blob);
    });
  }, true);
}
''';

// The native macOS sidebar/toolbar replace only the website's duplicated
// chrome. Article, directory, graph, forms, fonts and theme remain the website.
const desktopPageStyle = r'''
(() => {
  // The website's CSP has style-src 'self' and style-src-attr 'unsafe-inline'.
  // Inline element properties respect that policy; a new <style> does not.
  for (const element of document.querySelectorAll('.site-header, .skip, .footer')) {
    element.style.setProperty('display', 'none', 'important');
  }
  const shell = document.querySelector('.shell');
  const content = document.querySelector('.content');
  const readingLayout = document.querySelector('.reading-layout.with-article-navigation');
  const articleNavigation = readingLayout?.querySelector('.article-navigation');
  const article = readingLayout?.querySelector('.article');
  const outline = readingLayout?.querySelector('.reading-outline');
  const outlineSummary = outline?.querySelector('summary');
  const outlineLabel = outline?.querySelector('.outline-label');
  const outlineTop = outline?.querySelector('.outline-top');
  const outlineNav = outline?.querySelector('nav');
  const articleNavTree = articleNavigation?.querySelector('.article-nav-tree');
  let wideArticle = false;
  function applyStyles(element, styles, enabled) {
    if (!element) return;
    for (const [name, value] of Object.entries(styles)) {
      if (enabled) element.style.setProperty(name, value, 'important');
      else element.style.removeProperty(name);
    }
  }
  function fitContent() {
    const compact = window.innerWidth <= 900;
    shell?.style.setProperty('width', compact ? 'calc(100% - 40px)' : 'calc(100% - 72px)', 'important');
    content?.style.setProperty('padding-top', compact ? '24px' : '34px', 'important');
    // The site's three-column reading layout starts at 1350px. The native
    // sidebar reduces the WebView viewport, so use its existing columns at
    // a narrower desktop width while keeping the original DOM and controls.
    const showReadingRails = !!readingLayout && window.innerWidth >= 1000;
    applyStyles(readingLayout, {
      'display': 'grid', 'max-width': 'none',
      'grid-template-columns': '176px minmax(0, 1fr) 218px', 'gap': '24px'
    }, showReadingRails);
    applyStyles(articleNavigation, {
      'position': 'sticky', 'top': '24px', 'grid-column': '1', 'grid-row': '1',
      'align-self': 'start', 'order': 'initial', 'margin': '0',
      'padding': '0 15px 0 0', 'border': '0',
      'border-right': '1px solid var(--reading-line)'
    }, showReadingRails);
    applyStyles(articleNavTree, {
      'max-height': 'calc(100dvh - 160px)'
    }, showReadingRails);
    applyStyles(article, {
      'grid-column': '2', 'grid-row': '1', 'width': '100%'
    }, showReadingRails);
    applyStyles(outline, {
      'position': 'sticky', 'top': '24px', 'grid-column': '3', 'grid-row': '1',
      'align-self': 'start', 'order': 'initial', 'max-height': 'calc(100dvh - 48px)',
      'margin': '0', 'padding': '0 0 0 18px', 'overflow': 'auto',
      'border': '0', 'border-left': '1px solid var(--reading-line)'
    }, showReadingRails);
    applyStyles(outlineSummary, {'display': 'block'}, showReadingRails);
    applyStyles(outlineLabel, {'display': 'block'}, showReadingRails);
    applyStyles(outlineTop, {'display': 'block'}, showReadingRails);
    applyStyles(outlineNav, {'max-height': 'none'}, showReadingRails);
    if (showReadingRails && !wideArticle) {
      const articlePanel = articleNavigation?.querySelector('.article-nav-panel');
      const outlinePanel = outline?.querySelector('details');
      if (articlePanel) articlePanel.open = true;
      if (outlinePanel) outlinePanel.open = true;
    }
    wideArticle = showReadingRails;
  }
  shell?.style.setProperty('max-width', '1440px', 'important');
  shell?.style.setProperty('min-height', '100vh', 'important');
  shell?.style.setProperty('margin-inline', 'auto', 'important');
  fitContent();
  window.addEventListener('resize', fitContent);
  document.documentElement.style.setProperty('--header-offset', '0px');
})();
''';
