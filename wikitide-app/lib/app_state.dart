import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'api.dart';
import 'models.dart';

class AppState extends ChangeNotifier {
  AppState({WikiApi? api, FlutterSecureStorage? vault})
    : api = api ?? WikiApi(),
      vault =
          vault ??
          const FlutterSecureStorage(
            mOptions: MacOsOptions(usesDataProtectionKeychain: false),
          ) {
    this.api.onExpired = _expired;
  }
  final WikiApi api;
  final FlutterSecureStorage vault;
  Account? account;
  ThemeMode theme = ThemeMode.system;
  double fontSize = 17;
  int sessionEpoch = 0;
  String? startupError;
  SharedPreferences? _prefs;
  String get _sessionKey => 'session:${api.server}';
  String get draftPrefix => 'draft:${api.server}:${account?.id}:';

  Future<void> initialize() async {
    try {
      await _clearAttachments();
      _prefs = await SharedPreferences.getInstance();
      final server =
          _prefs!.getString('server') ??
          const String.fromEnvironment(
            'WIKITIDE_SERVER',
            defaultValue: defaultServer,
          );
      api.server = normalizeServer(server);
      theme = ThemeMode.values.firstWhere(
        (mode) => mode.name == _prefs!.getString('theme'),
        orElse: () => ThemeMode.system,
      );
      fontSize = (_prefs!.getDouble('fontSize') ?? 17).clamp(14, 24);
      api.token = await vault.read(key: _sessionKey);
      if (api.token != null) {
        final json = await api.call('GET', '/session');
        account = json['user'] == null
            ? null
            : Account.fromJson(json['user'] as Json);
      }
    } catch (error) {
      // An offline launch does not silently grant access to cached private data.
      startupError = error.toString();
      api.token = null;
    }
    notifyListeners();
  }

  Future<void> login(String username, String password) async {
    final json = await api.call(
      'POST',
      '/login',
      body: {'username': username, 'password': password},
      authenticated: false,
    );
    final token = json['token'] as String;
    try {
      await vault.write(key: _sessionKey, value: token);
    } catch (_) {
      // Revoke the newly created session if persistent secure storage fails.
      final previous = api.token;
      api.token = token;
      try {
        await api.call('POST', '/logout');
      } catch (_) {
        /* expires server-side */
      }
      api.token = previous;
      throw const ApiException('无法安全保存登录信息，请检查系统钥匙串后重试');
    }
    api.token = token;
    account = Account.fromJson(json['user'] as Json);
    startupError = null;
    sessionEpoch++;
    notifyListeners();
  }

  Future<void> logout() async {
    // Require server revocation before claiming a successful logout.
    if (api.token != null) await api.call('POST', '/logout');
    final prefix = draftPrefix;
    await vault.delete(key: _sessionKey);
    final draftKeys = (await vault.readAll()).keys
        .where((key) => key.startsWith(prefix))
        .toList();
    for (final key in draftKeys) {
      await vault.delete(key: key);
    }
    await _clearAttachments();
    api.token = null;
    account = null;
    sessionEpoch++;
    notifyListeners();
  }

  void _expired() {
    api.token = null;
    account = null;
    sessionEpoch++;
    startupError = '登录已过期，请重新登录';
    _clearAttachments();
    // Retain encrypted drafts for the same account to recover after login.
    notifyListeners();
  }

  Future<void> _clearAttachments() async {
    try {
      await const MethodChannel(
        'wikitide/attachments',
      ).invokeMethod<void>('clear');
    } on MissingPluginException {
      /* Unit tests do not load native plugins. */
    }
  }

  Future<void> setServer(String server) async {
    final normalized = normalizeServer(server);
    if (normalized == api.server) return;
    if (account != null) await logout();
    api.server = normalized;
    api.token = null;
    await _prefs?.setString('server', normalized);
    startupError = null;
    sessionEpoch++;
    notifyListeners();
  }

  Future<void> setTheme(ThemeMode mode) async {
    theme = mode;
    await _prefs?.setString('theme', mode.name);
    notifyListeners();
  }

  Future<void> setFontSize(double value) async {
    fontSize = value.clamp(14, 24);
    await _prefs?.setDouble('fontSize', fontSize);
    notifyListeners();
  }

  Future<Json?> readDraft(int id) async {
    final raw = await vault.read(key: '$draftPrefix$id');
    return raw == null ? null : jsonDecode(raw) as Json;
  }

  Future<void> writeDraft(int id, String body, String sha) => vault.write(
    key: '$draftPrefix$id',
    value: jsonEncode({'body': body, 'base_sha': sha}),
  );
  Future<void> deleteDraft(int id) => vault.delete(key: '$draftPrefix$id');
}
