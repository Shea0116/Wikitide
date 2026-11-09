import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api.dart';
import '../models.dart';
import 'agent_api.dart';
import 'local_memory_store.dart';
import 'memory_crypto.dart';

/// Website cookies never enter this session. Native requests use an independent
/// bearer stored in the platform keychain and verify it before every cloud call.
class AgentSession extends ChangeNotifier {
  AgentSession(String origin, {WikiApi? api, FlutterSecureStorage? vault})
    : api = api ?? WikiApi(server: origin),
      vault =
          vault ??
          const FlutterSecureStorage(
            mOptions: MacOsOptions(usesDataProtectionKeychain: false),
          ) {
    this.api.onExpired = _expired;
    transient = AgentTransientApi(this.api);
  }
  final WikiApi api;
  final FlutterSecureStorage vault;
  late final AgentTransientApi transient;
  LocalMemoryStore? store;
  Account? account;
  List<Account> boundAccounts = [];
  String? message;
  bool verified = false;
  int epoch = 0;
  bool get unlocked => store?.isUnlocked == true;
  String get _tokenKey => 'agent-session:${api.server}';
  String get _profilesKey => 'agent-profiles:${api.server}';
  String _keyName(int id) => 'agent-library-key:${api.server}:$id';

  Future<void> initialize() async {
    final prefs = await SharedPreferences.getInstance();
    try {
      final raw = jsonDecode(prefs.getString(_profilesKey) ?? '[]') as List;
      boundAccounts = raw
          .map((row) => Account.fromJson(Map<String, dynamic>.from(row as Map)))
          .toList();
    } catch (_) {
      boundAccounts = [];
    }
    // Reading the bearer is allowed at startup; library keys require the user's
    // explicit unlock action below, including when there is no network.
    try {
      api.token = await vault.read(key: _tokenKey);
    } catch (_) {
      message = '无法读取系统钥匙串；仍可检查站点后重试';
    }
    notifyListeners();
  }

  Future<void> login(String username, String password) async {
    lock();
    final response = await api.call(
      'POST',
      '/login',
      authenticated: false,
      body: {'username': username.trim(), 'password': password},
    );
    final token = response['token'] as String;
    final next = Account.fromJson(
      Map<String, dynamic>.from(response['user'] as Map),
    );
    final previous = api.token;
    try {
      await vault.write(key: _tokenKey, value: token);
    } catch (_) {
      api.token = token;
      try {
        await api.call('POST', '/logout');
      } catch (_) {
        /* server expiry */
      }
      api.token = previous;
      throw const ApiException('无法安全保存原生登录信息，请检查系统钥匙串');
    }
    api.token = token;
    account = next;
    verified = true;
    boundAccounts.removeWhere((row) => row.id == next.id);
    boundAccounts.add(next);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _profilesKey,
      jsonEncode(
        boundAccounts
            .map(
              (row) => {
                'id': row.id,
                'username': row.username,
                'role': row.role,
              },
            )
            .toList(),
      ),
    );
    message = null;
    notifyListeners();
  }

  Future<void> unlock(Account selected, {bool allowCreate = false}) async {
    lock();
    final currentEpoch = epoch;
    final raw = await vault.read(key: _keyName(selected.id));
    List<int> key;
    if (raw == null) {
      if (!allowCreate || !verified || account?.id != selected.id) {
        throw const LocalMemoryException('此账户尚无可解锁本机库，请联网登录后创建');
      }
      final hash = await MemoryCrypto.digest(Uri.parse(api.server).origin);
      final root = await getApplicationSupportDirectory();
      if (await File(
        '${root.path}/agent-memory/$hash/${selected.id}/memory.sqlite',
      ).exists()) {
        throw const LocalMemoryException('本机库存在但设备密钥丢失，请导入此前明文副本；不会覆盖原库');
      }
      key = MemoryCrypto.randomBytes(32);
      await vault.write(key: _keyName(selected.id), value: base64Encode(key));
    } else {
      key = base64Decode(raw);
    }
    if (epoch != currentEpoch) throw const LocalMemoryException('解锁已取消');
    final library = LocalMemoryStore(
      origin: api.server,
      accountId: selected.id,
    );
    try {
      await library.unlock(key);
      if (epoch != currentEpoch) {
        library.lock();
        throw const LocalMemoryException('解锁已取消');
      }
      account = selected;
      store = library;
      message = null;
    } finally {
      key.fillRange(0, key.length, 0);
    }
    notifyListeners();
  }

  Future<void> verifyOnline() async {
    final selected = account;
    if (selected == null || api.token == null) {
      throw const ApiException('请联网登录原生账户后使用云端问答');
    }
    final result = await api.call('GET', '/session');
    final current = Account.fromJson(
      Map<String, dynamic>.from(result['user'] as Map),
    );
    if (current.id != selected.id) {
      lock();
      throw const ApiException('原生登录与本机库账户不一致，请切换账户后重试');
    }
    verified = true;
  }

  void lock() {
    epoch++;
    transient.cancel();
    store?.lock();
    store = null;
    notifyListeners();
  }

  Future<void> logout() async {
    lock();
    // Lock immediately even when network revocation fails. Preserve the bearer
    // for a later retry and state the failure rather than claiming revocation.
    if (api.token != null) await api.call('POST', '/logout');
    await vault.delete(key: _tokenKey);
    api.token = null;
    account = null;
    verified = false;
    notifyListeners();
  }

  Future<void> deleteLibrary() async {
    final id = account?.id;
    final library = store;
    if (id == null || library == null) return;
    transient.cancel();
    await library.deleteLibrary();
    await vault.delete(key: _keyName(id));
    store = null;
    epoch++;
    notifyListeners();
  }

  void _expired() {
    lock();
    api.token = null;
    verified = false;
    message = '原生会话已失效，本机库已锁定。请重新登录；离线库可另行显式解锁';
    notifyListeners();
  }

  @override
  void dispose() {
    transient.cancel();
    store?.lock();
    api.close();
    super.dispose();
  }
}

/// Native editors participate in the existing macOS unsaved-content update
/// protection. It carries only a boolean; no local plaintext reaches WebView.
class NativeAgentReadiness {
  static final Set<Object> _blockers = {};
  static bool get canRestart => _blockers.isEmpty;
  static void setBlocked(Object owner, bool value) {
    if (value) {
      _blockers.add(owner);
    } else {
      _blockers.remove(owner);
    }
  }
}
