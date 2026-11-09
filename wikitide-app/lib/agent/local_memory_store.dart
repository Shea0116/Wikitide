import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqlite3/sqlite3.dart';
import 'memory_crypto.dart';

typedef MemoryJson = Map<String, dynamic>;

enum LocalRecordKind { memory, project, conversation }

class LocalMemoryException implements Exception {
  const LocalMemoryException(this.message);
  final String message;
  @override
  String toString() => message;
}

class LocalMemoryConflict extends LocalMemoryException {
  const LocalMemoryConflict() : super('内容已被另一窗口修改，请重新打开后再保存');
}

class LocalRecord {
  const LocalRecord({
    required this.id,
    required this.revision,
    required this.status,
    required this.payload,
    required this.updatedAt,
    this.projectId,
  });
  final String id;
  final int revision;
  final String status;
  final String? projectId;
  final MemoryJson payload;
  final int updatedAt;
  MemoryJson exportJson() => {
    'id': id,
    'revision': revision,
    'status': status,
    'project_id': projectId,
    'updated_at': updatedAt,
    'payload': payload,
  };
}

/// Short-lived worker isolates own the SQLite handle. No handle or key is
/// retained by the worker between operations, and no plaintext index is built.
class LocalMemoryStore {
  LocalMemoryStore({required this.origin, required this.accountId, this.root});
  final String origin;
  final int accountId;
  final Directory? root;
  Uint8List? _key;
  String? _path;
  String? _identity;
  int _epoch = 0;
  Future<void> _queue = Future<void>.value();
  bool get isUnlocked => _key != null;
  String? get databasePath => _path;

  Future<void> unlock(List<int> key) async {
    if (key.length != 32 || accountId <= 0) {
      throw const LocalMemoryException('本机密钥或账户无效');
    }
    lock();
    final epoch = _epoch;
    final identity = '${Uri.parse(origin).origin}|$accountId';
    final hash = await MemoryCrypto.digest(Uri.parse(origin).origin);
    final support = root ?? await getApplicationSupportDirectory();
    final directory = Directory(
      '${support.path}/agent-memory/$hash/$accountId',
    );
    await directory.create(recursive: true);
    if (epoch != _epoch) throw const LocalMemoryException('本机库已锁定');
    _key = Uint8List.fromList(key);
    _identity = identity;
    _path = '${directory.path}/memory.sqlite';
    try {
      await _operation('initialize');
    } catch (_) {
      lock();
      rethrow;
    }
  }

  void lock() {
    _epoch++;
    _key?.fillRange(0, _key!.length, 0);
    _key = null;
  }

  Future<dynamic> _operation(String operation, [MemoryJson? args]) {
    if (!isUnlocked) throw const LocalMemoryException('请先解锁本机记忆');
    final epoch = _epoch;
    final request = <String, dynamic>{
      'operation': operation,
      'path': _path,
      'identity': _identity,
      'key': Uint8List.fromList(_key!),
      ...?args,
    };
    final task = _queue.then((_) async {
      try {
        if (epoch != _epoch) throw const LocalMemoryException('本机库已锁定');
        final result = await _runDatabase(request);
        if (epoch != _epoch) throw const LocalMemoryException('本机库已锁定');
        if (result is Map && result['error'] != null) {
          if (result['error'] == 'conflict') throw const LocalMemoryConflict();
          throw LocalMemoryException(result['error'] as String);
        }
        return result;
      } finally {
        (request['key'] as Uint8List).fillRange(0, 32, 0);
      }
    });
    _queue = task.then<void>((_) {}, onError: (Object _) {});
    return task;
  }

  Future<List<LocalRecord>> list(LocalRecordKind kind) async {
    final rows = await _operation('list', {'kind': kind.name}) as List;
    return rows.map((raw) {
      final row = Map<String, dynamic>.from(raw as Map);
      return LocalRecord(
        id: row['id'] as String,
        revision: row['revision'] as int,
        status: row['status'] as String,
        projectId: row['project_id'] as String?,
        updatedAt: row['updated_at'] as int,
        payload: Map<String, dynamic>.from(row['payload'] as Map),
      );
    }).toList();
  }

  Future<LocalRecord> save(
    LocalRecordKind kind,
    MemoryJson payload, {
    String? id,
    int expectedRevision = 0,
    String status = 'active',
    String? projectId,
  }) async {
    validatePayload(kind, payload);
    final recordId = id ?? MemoryCrypto.newId();
    final result =
        await _operation('save', {
              'kind': kind.name,
              'id': recordId,
              'expected_revision': expectedRevision,
              'status': status,
              'project_id': projectId,
              'payload': payload,
            })
            as Map;
    return LocalRecord(
      id: recordId,
      revision: result['revision'] as int,
      status: status,
      projectId: projectId,
      payload: payload,
      updatedAt: result['updated_at'] as int,
    );
  }

  Future<void> remove(LocalRecordKind kind, LocalRecord record) => _operation(
    'remove',
    {'kind': kind.name, 'id': record.id, 'expected_revision': record.revision},
  ).then((_) {});

  Future<void> importNew(
    List<MemoryJson> projects,
    List<MemoryJson> memories,
  ) => _operation('import', {
    'projects': projects,
    'memories': memories,
  }).then((_) {});

  Future<void> deleteLibrary() async {
    final path = _path;
    lock();
    await _queue;
    if (path == null) return;
    await _deleteLibraryFiles(path);
  }

  static void validatePayload(LocalRecordKind kind, MemoryJson payload) {
    if (utf8.encode(jsonEncode(payload)).length > 128 * 1024) {
      throw const LocalMemoryException('单条内容超过 128 KiB');
    }
    if (kind == LocalRecordKind.memory) {
      final content = payload['content'];
      if (content is! String ||
          content.trim().isEmpty ||
          content.length > 4000) {
        throw const LocalMemoryException('记忆正文需要 1～4000 个字符');
      }
      if (!{
        'preference',
        'fact',
        'decision',
        'progress',
      }.contains(payload['kind'])) {
        throw const LocalMemoryException('记忆类型无效');
      }
      if (payload['source'] is! Map ||
          !{
            'manual',
            'import',
            'conversation',
          }.contains(payload['source']['kind'])) {
        throw const LocalMemoryException('记忆来源无效；团队资料不支持离线保存');
      }
      final expiry = payload['expires_at'];
      if (expiry != null && (expiry is! int || expiry <= 0)) {
        throw const LocalMemoryException('有效期格式无效');
      }
    }
    if (kind == LocalRecordKind.project &&
        (payload['title'] is! String ||
            (payload['title'] as String).trim().isEmpty ||
            (payload['title'] as String).length > 200)) {
      throw const LocalMemoryException('项目名称需要 1～200 个字符');
    }
  }
}

String _table(String kind) => switch (LocalRecordKind.values.byName(kind)) {
  LocalRecordKind.memory => 'local_memory_items',
  LocalRecordKind.project => 'local_projects',
  LocalRecordKind.conversation => 'local_conversations',
};

// Top-level closures capture only sendable request data, never a store/Future.
Future<dynamic> _runDatabase(MemoryJson request) =>
    Isolate.run(() => _databaseOperation(request));
Future<void> _deleteLibraryFiles(String path) => Isolate.run(() async {
  final directory = File(path).parent;
  if (await directory.exists()) await directory.delete(recursive: true);
});

Future<MemoryJson> _openRow(
  Map<String, dynamic> row,
  List<int> key,
  String identity,
  String table,
) => MemoryCrypto.open(
  row,
  key,
  MemoryCrypto.aad(
    identity,
    table,
    row['id'] as String,
    row['revision'] as int,
    status: row['status'] as String,
    projectId: row['project_id'] as String?,
  ),
);

Future<bool> _validMemoryRefs(
  Database db,
  MemoryJson payload,
  List<int> key,
  String identity,
  String? projectId,
) async {
  for (final ref in payload['memory_refs'] as List? ?? []) {
    final matches = db.select('SELECT * FROM local_memory_items WHERE id=?', [
      ref['local_id'],
    ]);
    if (matches.isEmpty ||
        matches.first['revision'] != ref['revision'] ||
        matches.first['status'] != 'active' ||
        (matches.first['project_id'] != null &&
            matches.first['project_id'] != projectId)) {
      return false;
    }
    final memory = await _openRow(
      matches.first,
      key,
      identity,
      'local_memory_items',
    );
    final expires = memory['expires_at'] as int?;
    if (expires != null && expires <= DateTime.now().millisecondsSinceEpoch) {
      return false;
    }
  }
  return true;
}

Future<dynamic> _databaseOperation(MemoryJson request) async {
  final database = sqlite3.open(request['path'] as String);
  final key = request['key'] as Uint8List;
  final identity = request['identity'] as String;
  try {
    database.execute('PRAGMA busy_timeout=5000');
    database.execute('PRAGMA secure_delete=ON');
    database.execute('PRAGMA journal_mode=WAL');
    database.execute(
      'CREATE TABLE IF NOT EXISTS local_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    );
    final schema = database.select(
      "SELECT value FROM local_meta WHERE key='schema_version'",
    );
    if (schema.isNotEmpty && schema.first['value'] != '1') {
      return {'error': '本机数据库版本较新，请更新客户端'};
    }
    database.execute(
      "INSERT OR IGNORE INTO local_meta VALUES ('schema_version', '1')",
    );
    final meta = database.select(
      "SELECT value FROM local_meta WHERE key='identity'",
    );
    if (meta.isNotEmpty && meta.first['value'] != identity) {
      return {'error': '本机库归属不匹配，已拒绝打开'};
    }
    database.execute(
      "INSERT OR IGNORE INTO local_meta VALUES ('identity', ?)",
      [identity],
    );
    for (final kind in LocalRecordKind.values) {
      database.execute('''CREATE TABLE IF NOT EXISTS ${_table(kind.name)} (
        id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER NOT NULL,
        status TEXT NOT NULL, key_id TEXT NOT NULL, nonce BLOB NOT NULL,
        ciphertext BLOB NOT NULL, mac BLOB NOT NULL, updated_at INTEGER NOT NULL
      )''');
    }
    final verification = database.select(
      "SELECT value FROM local_meta WHERE key='key_check'",
    );
    if (verification.isEmpty) {
      final check = await MemoryCrypto.seal(
        {'valid': true},
        key,
        MemoryCrypto.aad(identity, 'meta', 'check', 1),
      );
      database.execute("INSERT INTO local_meta VALUES ('key_check', ?)", [
        jsonEncode(
          check.map((k, v) => MapEntry(k, base64Encode(v as List<int>))),
        ),
      ]);
    } else {
      final sealed = (jsonDecode(verification.first['value'] as String) as Map)
          .map((k, v) => MapEntry(k as String, base64Decode(v as String)));
      await MemoryCrypto.open(
        sealed,
        key,
        MemoryCrypto.aad(identity, 'meta', 'check', 1),
      );
    }
    if (request['operation'] == 'initialize') return true;
    if (request['operation'] == 'import') {
      final prepared = <MemoryJson>[];
      for (final kind in [LocalRecordKind.project, LocalRecordKind.memory]) {
        final table = _table(kind.name);
        for (final raw
            in request[kind == LocalRecordKind.project
                    ? 'projects'
                    : 'memories']
                as List) {
          final row = Map<String, dynamic>.from(raw as Map);
          final payload = Map<String, dynamic>.from(row['payload'] as Map);
          payload['import_metadata'] = {
            'original_id': row['id'],
            'original_revision': row['revision'],
          };
          LocalMemoryStore.validatePayload(kind, payload);
          final sealed = await MemoryCrypto.seal(
            payload,
            key,
            MemoryCrypto.aad(
              identity,
              table,
              row['id'] as String,
              1,
              status: row['status'] as String,
              projectId: row['project_id'] as String?,
            ),
          );
          prepared.add({...row, 'table': table, 'sealed': sealed});
        }
      }
      database.execute('BEGIN IMMEDIATE');
      try {
        for (final table in ['local_projects', 'local_memory_items']) {
          final count =
              database.select('SELECT COUNT(*) AS n FROM $table').first['n']
                  as int;
          if (count + prepared.where((row) => row['table'] == table).length >
              1000) {
            throw const LocalMemoryException('导入超过本机条目限制');
          }
        }
        for (final row in prepared) {
          final table = row['table'] as String;
          if (database.select('SELECT id FROM $table WHERE id=?', [
            row['id'],
          ]).isNotEmpty) {
            database.execute('ROLLBACK');
            return {'error': 'conflict'};
          }
          if (row['project_id'] != null &&
              database.select('SELECT id FROM local_projects WHERE id=?', [
                row['project_id'],
              ]).isEmpty) {
            throw const LocalMemoryException('导入引用了缺失项目');
          }
          final sealed = row['sealed'] as Map;
          database.execute(
            "INSERT INTO $table VALUES (?, ?, 1, ?, 'v1', ?, ?, ?, ?)",
            [
              row['id'],
              row['project_id'],
              row['status'],
              sealed['nonce'],
              sealed['ciphertext'],
              sealed['mac'],
              DateTime.now().millisecondsSinceEpoch,
            ],
          );
        }
        database.execute('COMMIT');
        return true;
      } catch (_) {
        database.execute('ROLLBACK');
        rethrow;
      }
    }
    final table = _table(request['kind'] as String);
    if (request['operation'] == 'list') {
      final rows = database.select(
        'SELECT * FROM $table ORDER BY updated_at DESC LIMIT 1000',
      );
      final result = <MemoryJson>[];
      for (final row in rows) {
        final payload = await MemoryCrypto.open(
          row,
          key,
          MemoryCrypto.aad(
            identity,
            table,
            row['id'] as String,
            row['revision'] as int,
            status: row['status'] as String,
            projectId: row['project_id'] as String?,
          ),
        );
        if (table == 'local_conversations' &&
            !await _validMemoryRefs(
              database,
              payload,
              key,
              identity,
              row['project_id'] as String?,
            )) {
          database.execute('DELETE FROM local_conversations WHERE id=?', [
            row['id'],
          ]);
          continue;
        }
        result.add({
          'id': row['id'],
          'revision': row['revision'],
          'status': row['status'],
          'project_id': row['project_id'],
          'updated_at': row['updated_at'],
          'payload': payload,
        });
      }
      return result;
    }
    final id = request['id'] as String;
    final expected = request['expected_revision'] as int;
    final revision = expected + 1;
    final now = DateTime.now().millisecondsSinceEpoch;
    Map<String, Object>? sealed;
    if (request['operation'] == 'save') {
      if (!{'active', 'disabled', 'archived'}.contains(request['status'])) {
        return {'error': '本机状态无效'};
      }
      sealed = await MemoryCrypto.seal(
        Map<String, dynamic>.from(request['payload'] as Map),
        key,
        MemoryCrypto.aad(
          identity,
          table,
          id,
          revision,
          status: request['status'] as String,
          projectId: request['project_id'] as String?,
        ),
      );
    }
    database.execute('BEGIN IMMEDIATE');
    try {
      final existing = database.select(
        'SELECT revision FROM $table WHERE id=?',
        [id],
      );
      if ((existing.isEmpty ? 0 : existing.first['revision']) != expected) {
        database.execute('ROLLBACK');
        return {'error': 'conflict'};
      }
      if (request['operation'] == 'remove') {
        if (existing.isEmpty) {
          database.execute('ROLLBACK');
          return {'error': 'conflict'};
        }
        database.execute('DELETE FROM $table WHERE id=?', [id]);
        // A forgotten memory may be quoted in any local summary or answer.
        // Conservatively discard derived conversations in the same transaction.
        if (table == 'local_memory_items') {
          database.execute('DELETE FROM local_conversations');
        }
        if (table == 'local_projects') {
          database.execute(
            'DELETE FROM local_memory_items WHERE project_id=?',
            [id],
          );
          database.execute(
            'DELETE FROM local_conversations WHERE project_id=?',
            [id],
          );
        }
      } else {
        final count =
            database.select('SELECT COUNT(*) AS n FROM $table').first['n']
                as int;
        if (existing.isEmpty && count >= 1000) {
          database.execute('ROLLBACK');
          return {'error': '本机条目已满（每类 1000 条），请先导出或删除'};
        }
        if (request['project_id'] != null &&
            database.select(
              "SELECT id FROM local_projects WHERE id=? AND status='active'",
              [request['project_id']],
            ).isEmpty) {
          database.execute('ROLLBACK');
          return {'error': '所选项目不存在或已归档'};
        }
        if (table == 'local_conversations') {
          if (!await _validMemoryRefs(
            database,
            request['payload'] as MemoryJson,
            key,
            identity,
            request['project_id'] as String?,
          )) {
            database.execute('ROLLBACK');
            return {'error': '使用的本机记忆已变化或到期，本轮结果未保存，请重新选择记忆'};
          }
        }
        database.execute(
          '''INSERT INTO $table VALUES (?, ?, ?, ?, 'v1', ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id,
          revision=excluded.revision,status=excluded.status,nonce=excluded.nonce,
          ciphertext=excluded.ciphertext,mac=excluded.mac,updated_at=excluded.updated_at''',
          [
            id,
            request['project_id'],
            revision,
            request['status'],
            sealed!['nonce'],
            sealed['ciphertext'],
            sealed['mac'],
            now,
          ],
        );
        if (table == 'local_memory_items' && existing.isNotEmpty) {
          database.execute('DELETE FROM local_conversations');
        }
      }
      database.execute('COMMIT');
      return {'revision': revision, 'updated_at': now};
    } catch (_) {
      database.execute('ROLLBACK');
      rethrow;
    }
  } on SecretBoxAuthenticationError {
    return {'error': '本机密文校验失败，原记录已保留。请检查密钥或恢复导出副本'};
  } catch (_) {
    return {'error': '本机数据库操作失败，原记录已保留'};
  } finally {
    key.fillRange(0, key.length, 0);
    database.close();
  }
}
