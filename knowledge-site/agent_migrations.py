"""Additive agent schema, committed atomically without executescript side effects."""
import time


MIGRATIONS = {
    1: [
        '''CREATE TABLE agent_user_state (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          context_epoch INTEGER NOT NULL DEFAULT 0)''',
        '''CREATE TABLE agent_context_snapshots (
          id TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          session_id TEXT NOT NULL REFERENCES explore_sessions(id) ON DELETE CASCADE,
          base_session_version INTEGER NOT NULL, covered_message_id INTEGER NOT NULL,
          context_epoch INTEGER NOT NULL, constraints_version INTEGER NOT NULL,
          history_digest TEXT NOT NULL, summary_json TEXT NOT NULL,
          sources_json TEXT NOT NULL, estimated_tokens INTEGER NOT NULL,
          prompt_version TEXT NOT NULL, model_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'valid' CHECK(status IN ('valid','stale','invalid')),
          created_at INTEGER NOT NULL)''',
        'CREATE INDEX ix_agent_snapshots_session ON agent_context_snapshots(session_id,created_at)',
        '''CREATE TABLE agent_session_links (
          session_id TEXT PRIMARY KEY REFERENCES explore_sessions(id) ON DELETE CASCADE,
          current_snapshot_id TEXT REFERENCES agent_context_snapshots(id) ON DELETE SET NULL,
          pinned_json TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE agent_context_metrics (
          id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, status TEXT NOT NULL,
          compacted INTEGER NOT NULL, degraded INTEGER NOT NULL,
          summary_requested INTEGER NOT NULL, summary_ms INTEGER NOT NULL,
          estimated_input_tokens INTEGER NOT NULL,
          summary_prompt_tokens INTEGER, summary_completion_tokens INTEGER)''',
    ],
    2: ['ALTER TABLE agent_context_snapshots ADD COLUMN compression_depth INTEGER NOT NULL DEFAULT 1'],
    3: ['''CREATE TABLE agent_answer_context (
        message_id INTEGER PRIMARY KEY REFERENCES explore_messages(id) ON DELETE CASCADE,
        sources_json TEXT NOT NULL DEFAULT '[]')'''],
    4: [
        '''CREATE TABLE agent_projects (
          id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL,goal TEXT NOT NULL DEFAULT '',progress TEXT NOT NULL DEFAULT '',
          next_action TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',
          version INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)''',
        'CREATE INDEX ix_agent_project_owner ON agent_projects(user_id,updated_at)',
        '''CREATE TABLE agent_project_links (
          project_id TEXT NOT NULL REFERENCES agent_projects(id) ON DELETE CASCADE,
          resource_kind TEXT NOT NULL,resource_id TEXT NOT NULL,created_at INTEGER NOT NULL,
          PRIMARY KEY(project_id,resource_kind,resource_id))''',
        'ALTER TABLE agent_session_links ADD COLUMN project_id TEXT REFERENCES agent_projects(id) ON DELETE SET NULL',
        '''CREATE TABLE agent_memory_items (
          id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          project_id TEXT REFERENCES agent_projects(id) ON DELETE CASCADE,
          scope TEXT NOT NULL CHECK(scope IN ('account','project')),
          kind TEXT NOT NULL CHECK(kind IN ('preference','project_fact','experience')),
          content TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('candidate','active','disabled','stale','deleted')),
          source_json TEXT NOT NULL DEFAULT '{}',conflict_key TEXT NOT NULL DEFAULT '',
          revision INTEGER NOT NULL DEFAULT 1,expires_at INTEGER,supersedes_id TEXT,
          created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
          CHECK((scope='account' AND project_id IS NULL) OR (scope='project' AND project_id IS NOT NULL)))''',
        'CREATE INDEX ix_agent_memory_owner ON agent_memory_items(user_id,project_id,status,updated_at)',
        '''CREATE TABLE agent_memory_events(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id)
          ON DELETE CASCADE,memory_id TEXT NOT NULL,action TEXT NOT NULL,revision INTEGER NOT NULL,created_at INTEGER NOT NULL)''',
        '''CREATE TABLE agent_memory_suppressions(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          source_message_id INTEGER NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(user_id,source_message_id))''',
        '''CREATE TABLE agent_artifact_dependencies(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          source_kind TEXT NOT NULL,source_id TEXT NOT NULL,source_revision TEXT NOT NULL DEFAULT '',
          target_kind TEXT NOT NULL,target_id TEXT NOT NULL,
          PRIMARY KEY(user_id,source_kind,source_id,source_revision,target_kind,target_id))''',
        'CREATE INDEX ix_agent_deps_source ON agent_artifact_dependencies(source_kind,source_id)',
        '''CREATE TABLE agent_session_handoffs(session_id TEXT PRIMARY KEY REFERENCES explore_sessions(id) ON DELETE CASCADE,
          payload_json TEXT NOT NULL,sources_json TEXT NOT NULL,context_epoch INTEGER NOT NULL)''',
        "ALTER TABLE agent_context_snapshots ADD COLUMN memory_mode TEXT NOT NULL DEFAULT 'off'",
        'ALTER TABLE agent_answer_context ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 0',
        "ALTER TABLE agent_answer_context ADD COLUMN memory_refs_json TEXT NOT NULL DEFAULT '[]'",
        '''CREATE TABLE agent_draft_context(draft_id TEXT PRIMARY KEY REFERENCES explore_drafts(id) ON DELETE CASCADE,
          project_id TEXT REFERENCES agent_projects(id) ON DELETE SET NULL,sources_json TEXT NOT NULL DEFAULT '[]',
          memory_mode TEXT NOT NULL DEFAULT 'account')''',
    ],
    5: [
        '''CREATE TABLE agent_runs(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          project_id TEXT REFERENCES agent_projects(id) ON DELETE SET NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,input_json TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}',
          cancel_requested INTEGER NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',lease_generation INTEGER NOT NULL DEFAULT 0,
          lease_until INTEGER NOT NULL DEFAULT 0,heartbeat_at INTEGER NOT NULL DEFAULT 0,context_epoch INTEGER NOT NULL,
          created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,error_code TEXT NOT NULL DEFAULT '',
          UNIQUE(user_id,idempotency_key))''',
        'CREATE INDEX ix_agent_runs_queue ON agent_runs(status,created_at)',
        '''CREATE TABLE agent_run_steps(run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
          step_key TEXT NOT NULL,status TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,input_fingerprint TEXT NOT NULL,
          output_json TEXT NOT NULL DEFAULT '{}',provider_started_at INTEGER,provider_completed_at INTEGER,
          error_code TEXT NOT NULL DEFAULT '',updated_at INTEGER NOT NULL,PRIMARY KEY(run_id,step_key))''',
        '''CREATE TABLE agent_run_events(run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
          seq INTEGER NOT NULL,type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at INTEGER NOT NULL,
          PRIMARY KEY(run_id,seq))''',
        '''CREATE TABLE agent_run_budget(run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
          reserved_tokens INTEGER NOT NULL DEFAULT 0,used_tokens INTEGER NOT NULL DEFAULT 0,
          search_calls INTEGER NOT NULL DEFAULT 0,max_tokens INTEGER NOT NULL,max_search_calls INTEGER NOT NULL)''',
        '''CREATE TABLE agent_note_versions(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          body_sha TEXT NOT NULL,sections_json TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(note_id,body_sha))''',
    ],
    6: [
        '''CREATE TABLE agent_usage_reservations(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind TEXT NOT NULL,reserved_tokens INTEGER NOT NULL,charged_tokens INTEGER,status TEXT NOT NULL DEFAULT 'reserved',created_at INTEGER NOT NULL)''',
        'CREATE INDEX ix_agent_usage_owner ON agent_usage_reservations(user_id,created_at)',
    ],
}


def migrate_agent(db):
    # Existing initializers may have pending work. Commit it before our own migration.
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS agent_schema_migrations (
            version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)''')
        applied = {row[0] for row in db.execute('SELECT version FROM agent_schema_migrations')}
        if applied - MIGRATIONS.keys():
            raise RuntimeError('Agent database schema is newer than this application')
        for version, statements in MIGRATIONS.items():
            if version in applied:
                continue
            for statement in statements:
                db.execute(statement)
            db.execute('INSERT INTO agent_schema_migrations VALUES(?,?)', (version, int(time.time())))
        db.commit()
    except BaseException:
        db.rollback()
        raise
