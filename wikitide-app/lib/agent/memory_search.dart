import 'local_memory_store.dart';

bool memoryIsUsable(LocalRecord row, {String? projectId, DateTime? now}) {
  final expiresAt = row.payload['expires_at'] as int?;
  return row.status == 'active' &&
      (row.projectId == null || row.projectId == projectId) &&
      (expiresAt == null ||
          expiresAt > (now ?? DateTime.now()).millisecondsSinceEpoch);
}

/// Only searches decrypted, bounded in-memory records. Never writes plaintext
/// FTS, embeddings or search queries into the SQLite library.
List<LocalRecord> searchMemories(
  List<LocalRecord> records,
  String query, {
  String? projectId,
  int limit = 5,
  DateTime? now,
}) {
  final terms = RegExp(
    r'[a-z0-9_]+|[\u4e00-\u9fff]{1,2}',
    caseSensitive: false,
  ).allMatches(query.toLowerCase()).map((match) => match.group(0)!).toSet();
  int score(LocalRecord item) {
    final text = '${item.payload['title'] ?? ''} ${item.payload['content']}'
        .toLowerCase();
    return terms.where(text.contains).length * 10 +
        (item.projectId == projectId && projectId != null ? 2 : 0) +
        (item.payload['kind'] == 'preference' ? 1 : 0);
  }

  final candidates = records
      .where((row) => memoryIsUsable(row, projectId: projectId, now: now))
      .toList();
  candidates.sort((a, b) {
    final byScore = score(b).compareTo(score(a));
    return byScore == 0 ? b.updatedAt.compareTo(a.updatedAt) : byScore;
  });
  return candidates.take(limit.clamp(0, 5)).toList();
}

class MemoryImportPreview {
  const MemoryImportPreview(this.projects, this.memories, this.conflicts);
  final List<MemoryJson> projects;
  final List<MemoryJson> memories;
  final List<String> conflicts;
  int get newCount => projects.length + memories.length;
}

class MemoryTransfer {
  static const maxBytes = 8 * 1024 * 1024;

  static MemoryJson exportBundle({
    required String origin,
    required int accountId,
    required List<LocalRecord> memories,
    required List<LocalRecord> projects,
  }) => {
    'schema_version': 1,
    'format': 'wikitide-local-memory',
    'origin': origin,
    'account_id': accountId,
    'exported_at': DateTime.now().toUtc().toIso8601String(),
    'projects': projects.map((row) => row.exportJson()).toList(),
    'memories': memories.map((row) => row.exportJson()).toList(),
  };

  static MemoryImportPreview preview(
    MemoryJson bundle, {
    required List<LocalRecord> existingProjects,
    required List<LocalRecord> existingMemories,
  }) {
    if (bundle['schema_version'] != 1 ||
        bundle['format'] != 'wikitide-local-memory') {
      throw const LocalMemoryException('只支持 WikiTide 本机记忆 JSON v1');
    }
    final conflicts = <String>[];
    final projectIds = existingProjects.map((row) => row.id).toSet();
    final memoryIds = existingMemories.map((row) => row.id).toSet();
    List<MemoryJson> check(
      String field,
      LocalRecordKind kind,
      Set<String> ids,
    ) {
      final rows = bundle[field];
      if (rows is! List || rows.length > 1000) {
        throw const LocalMemoryException('导入条目无效或超过 1000 条');
      }
      final added = <MemoryJson>[];
      final seen = <String>{};
      for (final raw in rows) {
        if (raw is! Map) throw const LocalMemoryException('导入记录格式无效');
        final row = Map<String, dynamic>.from(raw);
        final id = row['id'];
        if (id is! String ||
            !RegExp(r'^[A-Za-z0-9_-]{1,100}$').hasMatch(id) ||
            !seen.add(id)) {
          throw const LocalMemoryException('导入 ID 无效或重复');
        }
        if (row['revision'] is! int ||
            (row['revision'] as int) < 1 ||
            !{'active', 'disabled', 'archived'}.contains(row['status']) ||
            row['payload'] is! Map ||
            (row['project_id'] != null && row['project_id'] is! String)) {
          throw const LocalMemoryException('导入版本、状态或内容无效');
        }
        final payload = Map<String, dynamic>.from(row['payload'] as Map);
        LocalMemoryStore.validatePayload(kind, payload);
        if (ids.contains(id)) {
          conflicts.add(id);
        } else {
          added.add({...row, 'payload': payload});
        }
      }
      return added;
    }

    final projects = check('projects', LocalRecordKind.project, projectIds);
    final memories = check('memories', LocalRecordKind.memory, memoryIds);
    final allowedProjectIds = {
      ...projectIds,
      ...projects.map((row) => row['id'] as String),
    };
    for (final row in memories) {
      if (row['project_id'] != null &&
          !allowedProjectIds.contains(row['project_id'])) {
        throw const LocalMemoryException('导入记忆引用了缺失的项目');
      }
    }
    if (existingProjects.length + projects.length > 1000 ||
        existingMemories.length + memories.length > 1000) {
      throw const LocalMemoryException('导入后超过本机 1000 条限制');
    }
    return MemoryImportPreview(projects, memories, conflicts);
  }

  static String markdown(
    List<LocalRecord> memories,
    List<LocalRecord> projects,
  ) {
    final names = {for (final row in projects) row.id: row.payload['title']};
    final text = StringBuffer('# WikiTide 本机记忆导出\n\n此文件是明文副本，请妥善保存。\n\n');
    for (final row in memories) {
      text.writeln('## ${row.payload['title'] ?? '记忆'}\n');
      text.writeln(
        '- ID：${row.id}\n- 版本：${row.revision}\n- 状态：${row.status}\n- 范围：${row.projectId == null ? '账户' : names[row.projectId] ?? row.projectId}\n- 类型：${row.payload['kind']}\n- 来源：${row.payload['source']}\n',
      );
      text.writeln('${row.payload['content']}\n');
    }
    return text.toString();
  }
}
