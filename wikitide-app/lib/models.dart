typedef Json = Map<String, dynamic>;

class Account {
  const Account(this.id, this.username, this.role);
  factory Account.fromJson(Json json) => Account(
    json['id'] as int,
    json['username'] as String,
    json['role'] as String,
  );
  final int id;
  final String username;
  final String role;
}

class Note {
  Note.fromJson(Json json)
    : id = json['id'] as int,
      title = json['title'] as String,
      summary = json['summary'] as String? ?? '',
      category = json['category'] as String? ?? '',
      isPrivate = json['visibility'] == 'private',
      position = (json['position'] as num?)?.toDouble() ?? 0,
      snippet = json['snippet_html'] as String? ?? '';
  final int id;
  final String title, summary, category, snippet;
  final bool isPrivate;
  final double position;
}

class NoteDetail {
  NoteDetail.fromJson(Json json)
    : note = Note.fromJson(json),
      html = json['html'] as String,
      body = json['body'] as String? ?? '',
      sha = json['body_sha'] as String,
      editable = json['editable'] == true,
      bookmarked = json['reading']?['bookmarked'] == 1,
      position = (json['reading']?['position'] as num?)?.toDouble() ?? 0,
      related = notesFrom(json['related']),
      backlinks = notesFrom(json['backlinks']);
  final Note note;
  final String html, body, sha;
  final bool editable, bookmarked;
  final double position;
  final List<Note> related, backlinks;
}

List<Note> notesFrom(dynamic value) => (value as List? ?? [])
    .map((item) => Note.fromJson(Map<String, dynamic>.from(item as Map)))
    .toList();

class Category {
  Category.fromJson(Json json)
    : name = json['name'] as String,
      path = json['path'] as String,
      total = json['total'] as int,
      children = (json['children'] as List? ?? [])
          .map(
            (item) => Category.fromJson(Map<String, dynamic>.from(item as Map)),
          )
          .toList();
  final String name, path;
  final int total;
  final List<Category> children;
}

class NotePage {
  NotePage.fromJson(Json json)
    : notes = notesFrom(json['notes']),
      total = json['total'] as int,
      hasMore = json['has_more'] == true;
  final List<Note> notes;
  final int total;
  final bool hasMore;
}
