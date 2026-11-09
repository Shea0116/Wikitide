import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:wikitide/agent/local_memory_store.dart';
import 'package:wikitide/agent/memory_crypto.dart';
import 'package:wikitide/agent/memory_search.dart';

void main() {
  late Directory root;
  final key = Uint8List.fromList(List.generate(32, (i) => i + 1));
  late LocalMemoryStore store;
  Map<String, dynamic> payload(String text) => {
    'title': '本机记忆',
    'kind': 'preference',
    'content': text,
    'source': {'kind': 'manual'},
    'expires_at': null,
  };
  setUp(() async {
    root = await Directory.systemTemp.createTemp('wikitide-agent-memory-');
    store = LocalMemoryStore(
      origin: 'https://example.test',
      accountId: 1,
      root: root,
    );
    await store.unlock(key);
  });
  tearDown(() async {
    store.lock();
    await root.delete(recursive: true);
  });

  test(
    'AES-GCM uses fresh nonce and authenticates identity, revision and metadata',
    () async {
      final aad = MemoryCrypto.aad(
        'site|1',
        'memory',
        'one',
        1,
        status: 'active',
      );
      final a = await MemoryCrypto.seal(payload('secret'), key, aad);
      final b = await MemoryCrypto.seal(payload('secret'), key, aad);
      expect(a['nonce'], isNot(equals(b['nonce'])));
      expect((await MemoryCrypto.open(a, key, aad))['content'], 'secret');
      for (final altered in [
        MemoryCrypto.aad('site|2', 'memory', 'one', 1, status: 'active'),
        MemoryCrypto.aad('site|1', 'memory', 'one', 2, status: 'active'),
        MemoryCrypto.aad('site|1', 'memory', 'one', 1, status: 'disabled'),
      ]) {
        await expectLater(
          MemoryCrypto.open(a, key, altered),
          throwsA(isA<SecretBoxAuthenticationError>()),
        );
      }
    },
  );

  test(
    'encrypted SQLite persists offline and rejects stale revisions',
    () async {
      final record = await store.save(
        LocalRecordKind.memory,
        payload('LOCAL_SECRET_MARKER'),
      );
      final db = sqlite3.open(store.databasePath!);
      final row = db.select('SELECT * FROM local_memory_items').single;
      expect(row.keys, isNot(contains('content')));
      expect(
        utf8.decode(row['ciphertext'] as List<int>, allowMalformed: true),
        isNot(contains('LOCAL_SECRET_MARKER')),
      );
      db.close();
      store.lock();
      await store.unlock(key);
      expect(
        (await store.list(LocalRecordKind.memory)).single.payload['content'],
        'LOCAL_SECRET_MARKER',
      );
      await store.save(
        LocalRecordKind.memory,
        payload('new'),
        id: record.id,
        expectedRevision: 1,
      );
      await expectLater(
        store.save(
          LocalRecordKind.memory,
          payload('stale'),
          id: record.id,
          expectedRevision: 1,
        ),
        throwsA(isA<LocalMemoryConflict>()),
      );
      expect(
        (await store.list(LocalRecordKind.memory)).single.payload['content'],
        'new',
      );
      store.lock();
      expect(
        () => store.list(LocalRecordKind.memory),
        throwsA(isA<LocalMemoryException>()),
      );
    },
  );

  test(
    'tampering and copying library to another account fail without overwriting ciphertext',
    () async {
      await store.save(LocalRecordKind.memory, payload('keep-original'));
      final path = store.databasePath!;
      final db = sqlite3.open(path);
      db.execute("UPDATE local_memory_items SET status='disabled'");
      final original = List<int>.from(
        db
                .select('SELECT ciphertext FROM local_memory_items')
                .single['ciphertext']
            as List<int>,
      );
      db.close();
      await expectLater(
        store.list(LocalRecordKind.memory),
        throwsA(isA<LocalMemoryException>()),
      );
      final check = sqlite3.open(path);
      expect(
        check
            .select('SELECT ciphertext FROM local_memory_items')
            .single['ciphertext'],
        original,
      );
      check.close();
      store.lock();
      final other = LocalMemoryStore(
        origin: 'https://example.test',
        accountId: 2,
        root: root,
      );
      await other.unlock(key);
      final otherPath = other.databasePath!;
      other.lock();
      // SQLite operation handles close after every command, including WAL checkpoint.
      await File(path).copy(otherPath);
      await expectLater(
        other.unlock(key),
        throwsA(isA<LocalMemoryException>()),
      );
      expect(other.isUnlocked, false);
      await expectLater(
        store.unlock(List.filled(32, 0)),
        throwsA(isA<LocalMemoryException>()),
      );
    },
  );

  test(
    'forgetting clears derived conversations and stale in-flight saves fail',
    () async {
      final memory = await store.save(
        LocalRecordKind.memory,
        payload('forget me'),
      );
      final body = {
        'history': [
          {'role': 'user', 'content': 'private history'},
        ],
        'summary': {'goal': 'private summary'},
        'memory_refs': [
          {'local_id': memory.id, 'revision': 1},
        ],
      };
      final conversation = await store.save(LocalRecordKind.conversation, body);
      await store.remove(LocalRecordKind.memory, memory);
      expect(await store.list(LocalRecordKind.conversation), isEmpty);
      await expectLater(
        store.save(
          LocalRecordKind.conversation,
          body,
          id: conversation.id,
          expectedRevision: conversation.revision,
        ),
        throwsA(isA<LocalMemoryConflict>()),
      );
      await expectLater(
        store.save(LocalRecordKind.conversation, body),
        throwsA(isA<LocalMemoryException>()),
      );
    },
  );

  test(
    'project isolation, expiry and bounded search exclude unavailable memory',
    () async {
      final project = await store.save(LocalRecordKind.project, {'title': 'A'});
      await store.save(LocalRecordKind.memory, payload('account'));
      await store.save(
        LocalRecordKind.memory,
        payload('project'),
        projectId: project.id,
      );
      await store.save(LocalRecordKind.memory, {
        ...payload('expired'),
        'expires_at': 1,
      });
      final rows = await store.list(LocalRecordKind.memory);
      expect(searchMemories(rows, 'project').map((r) => r.payload['content']), [
        'account',
      ]);
      expect(
        searchMemories(
          rows,
          'project',
          projectId: project.id,
        ).first.payload['content'],
        'project',
      );
      await store.remove(LocalRecordKind.project, project);
      expect(
        (await store.list(
          LocalRecordKind.memory,
        )).any((r) => r.projectId == project.id),
        false,
      );
    },
  );

  test(
    'import preview preserves revisions as metadata and never overwrites conflicts',
    () async {
      final memory = await store.save(
        LocalRecordKind.memory,
        payload('original'),
      );
      final bundle = MemoryTransfer.exportBundle(
        origin: 'https://example.test',
        accountId: 1,
        memories: [memory],
        projects: [],
      );
      final preview = MemoryTransfer.preview(
        bundle,
        existingProjects: [],
        existingMemories: [memory],
      );
      expect(preview.conflicts, [memory.id]);
      expect(preview.newCount, 0);
      final other = LocalMemoryStore(
        origin: 'https://example.test',
        accountId: 2,
        root: root,
      );
      await other.unlock(key);
      final fresh = MemoryTransfer.preview(
        bundle,
        existingProjects: [],
        existingMemories: [],
      );
      await other.importNew(fresh.projects, fresh.memories);
      final imported = (await other.list(LocalRecordKind.memory)).single;
      expect(imported.payload['content'], 'original');
      expect(imported.payload['import_metadata']['original_revision'], 1);
      await expectLater(
        other.importNew(fresh.projects, fresh.memories),
        throwsA(isA<LocalMemoryConflict>()),
      );
      expect((await other.list(LocalRecordKind.memory)).length, 1);
      other.lock();
    },
  );
}
