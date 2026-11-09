import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../design.dart';
import '../models.dart';
import 'common.dart';

class LibraryHome extends StatelessWidget {
  const LibraryHome({
    super.key,
    required this.data,
    required this.onCategory,
    required this.onNote,
    required this.onLibrary,
    required this.refresh,
    this.warning,
  });
  final Json data;
  final void Function(String) onCategory;
  final void Function(Note) onNote;
  final VoidCallback onLibrary;
  final Future<void> Function() refresh;
  final String? warning;
  @override
  Widget build(BuildContext context) {
    final design = WikiDesign(context);
    final categories = (data['categories'] as List)
        .map((item) => Category.fromJson(item as Json))
        .toList();
    final continuing = notesFrom(data['continue_reading']),
        recent = notesFrom(data['recent']);
    final guides = notesFrom(data['guides']);
    return RefreshIndicator(
      onRefresh: refresh,
      child: LayoutBuilder(
        builder: (context, viewport) {
          final compact = viewport.maxWidth < 650;
          final padding = compact
              ? 24.0
              : (viewport.maxWidth * .06).clamp(28.0, 72.0);
          return ListView(
            padding: EdgeInsets.fromLTRB(padding, 0, padding, 60),
            children: [
              Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1240),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (warning != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 16),
                          child: Text(
                            warning!,
                            style: TextStyle(color: design.muted),
                          ),
                        ),
                      LayoutBuilder(
                        builder: (context, space) => _Hero(
                          width: space.maxWidth,
                          categories: categories,
                          browse: () => onCategory(''),
                          latest: () => onCategory(''),
                        ),
                      ),
                      if (continuing.isNotEmpty) ...[
                        _Heading('继续阅读', 'CONTINUE READING', '我的阅读', onLibrary),
                        ...continuing.map(
                          (note) =>
                              NoteTile(note: note, onTap: () => onNote(note)),
                        ),
                      ],
                      _Heading(
                        '从一个方向开始',
                        '01 / EXPLORE',
                        compact ? '知识地图 ↗' : '浏览知识地图 ↗',
                        () => onCategory(''),
                      ),
                      LayoutBuilder(
                        builder: (context, space) {
                          final columns = math.max(
                            1,
                            math.min(3, (space.maxWidth / 240).floor()),
                          );
                          final width =
                              (space.maxWidth - (columns - 1) * 18) / columns;
                          return Wrap(
                            spacing: 18,
                            runSpacing: 18,
                            children: [
                              for (int i = 0; i < categories.length; i++)
                                SizedBox(
                                  width: width,
                                  child: _Collection(
                                    category: categories[i],
                                    index: i + 1,
                                    open: () => onCategory(categories[i].path),
                                  ),
                                ),
                            ],
                          );
                        },
                      ),
                      _Heading(
                        '最近更新',
                        '02 / LATEST NOTES',
                        '全部笔记 ↗',
                        () => onCategory(''),
                      ),
                      Container(
                        decoration: BoxDecoration(
                          border: Border(top: BorderSide(color: design.line)),
                        ),
                        child: Column(
                          children: [
                            if (recent.isEmpty) const EmptyState('暂无笔记。'),
                            for (int i = 0; i < math.min(recent.length, 6); i++)
                              _NoteRow(
                                note: recent[i],
                                index: i + 1,
                                open: () => onNote(recent[i]),
                              ),
                          ],
                        ),
                      ),
                      if (guides.isNotEmpty) ...[
                        _Heading(
                          '知识导览',
                          '03 / FIND YOUR WAY',
                          '查看全部 ↗',
                          () => onCategory('知识导览'),
                        ),
                        ...guides
                            .take(3)
                            .map(
                              (note) => NoteTile(
                                note: note,
                                onTap: () => onNote(note),
                              ),
                            ),
                      ],
                      const SizedBox(height: 72),
                      Divider(color: design.line),
                      const SizedBox(height: 28),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text('识汐', style: design.kai(24)),
                          const SizedBox(width: 14),
                          Text('WIKITIDE', style: design.mono(10)),
                        ],
                      ),
                      const SizedBox(height: 14),
                      Text('每一次回看，都有新的发现。', style: design.kai(17)),
                      const SizedBox(height: 14),
                      Text(
                        '内容以原始笔记为准。技术、医疗和法律信息请核对更新日期及原始来源。',
                        style: TextStyle(
                          fontSize: 10,
                          height: 1.7,
                          color: design.muted,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({
    required this.width,
    required this.categories,
    required this.browse,
    required this.latest,
  });
  final double width;
  final List<Category> categories;
  final VoidCallback browse, latest;
  @override
  Widget build(BuildContext context) {
    final d = WikiDesign(context);
    final narrow = width < 600;
    final font = narrow ? 48.0 : (width * .072).clamp(48.0, 76.0);
    final copy = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(width: 29, height: 1, color: const Color(0xff9d89b9)),
            const SizedBox(width: 12),
            Flexible(
              child: Text(
                'A PERSONAL KNOWLEDGE LIBRARY',
                style: d.mono(narrow ? 8 : 9),
                maxLines: 1,
              ),
            ),
          ],
        ),
        const SizedBox(height: 30),
        Text.rich(
          TextSpan(
            children: [
              const TextSpan(text: '让知识\n有迹可循'),
              TextSpan(
                text: '。',
                style: TextStyle(color: d.accent),
              ),
            ],
          ),
          style: d.song(font),
          semanticsLabel: '让知识有迹可循',
        ),
        const SizedBox(height: 25),
        Text(
          '把零散的灵感，整理成可以回来的地方。\n在这里，阅读、连接，再发现。',
          style: TextStyle(fontSize: 14, color: d.muted, height: 1.9),
        ),
        const SizedBox(height: 28),
        Wrap(
          spacing: 22,
          runSpacing: 10,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            FilledButton(
              onPressed: browse,
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.fromLTRB(25, 9, 10, 9),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    '开始探索',
                    style: d
                        .kai(20)
                        .copyWith(
                          color: d.dark
                              ? const Color(0xff251633)
                              : Colors.white,
                        ),
                  ),
                  const SizedBox(width: 24),
                  Container(
                    width: 34,
                    height: 34,
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: .09),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.north_east, size: 16),
                  ),
                ],
              ),
            ),
            TextButton(
              onPressed: latest,
              child: Text(
                '看看最近更新  ↓',
                style: TextStyle(fontSize: 12, color: d.muted),
              ),
            ),
          ],
        ),
      ],
    );
    final art = SizedBox(
      width: narrow ? 245 : width * .43,
      child: Column(
        children: [
          AspectRatio(
            aspectRatio: 1,
            child: CustomPaint(painter: TideOrb(d.dark)),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 30),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Flexible(
                  child: Text(
                    'THOUGHTS IN FLOW',
                    style: d.mono(8),
                    maxLines: 1,
                    overflow: TextOverflow.fade,
                    softWrap: false,
                  ),
                ),
                Text('识 · 汐', style: d.kai(14)),
              ],
            ),
          ),
        ],
      ),
    );
    return Column(
      children: [
        Padding(
          padding: EdgeInsets.only(
            top: narrow ? 40 : 54,
            bottom: narrow ? 32 : 40,
          ),
          child: narrow
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    copy,
                    const SizedBox(height: 24),
                    Align(alignment: Alignment.centerRight, child: art),
                  ],
                )
              : ConstrainedBox(
                  constraints: const BoxConstraints(minHeight: 400),
                  child: Row(
                    children: [
                      Expanded(child: copy),
                      const SizedBox(width: 20),
                      art,
                    ],
                  ),
                ),
        ),
        Container(
          padding: const EdgeInsets.symmetric(vertical: 24),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: d.line)),
          ),
          child: Wrap(
            alignment: WrapAlignment.spaceBetween,
            spacing: 24,
            runSpacing: 10,
            children: [
              Text(
                '一座持续生长的知识库',
                style: TextStyle(fontSize: 11, color: d.muted),
              ),
              Text(
                '${categories.fold<int>(0, (sum, c) => sum + c.total)} 篇可见笔记   /   ${categories.length} 个方向',
                style: TextStyle(fontSize: 11, color: d.muted),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class TideOrb extends CustomPainter {
  TideOrb(this.dark);
  final bool dark;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 460, size.height / 460);
    final orbit = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = .75
      ..color = Color(dark ? 0xff69547e : 0xffcbbddf);
    const center = Offset(230, 230);
    canvas.drawCircle(center, 184, orbit);
    final halo = Paint()..color = Color(dark ? 0xff4b3d5d : 0xffded5ed);
    for (double angle = 0; angle < math.pi * 2; angle += .038) {
      canvas.drawCircle(
        center + Offset(math.cos(angle) * 211, math.sin(angle) * 211),
        .7,
        halo,
      );
    }
    canvas.save();
    canvas.clipPath(
      Path()..addOval(Rect.fromCircle(center: center, radius: 184)),
    );
    final wave = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..shader = LinearGradient(
        colors: [
          Color(dark ? 0xff9d82c7 : 0xffb3a3df),
          Color(dark ? 0xffc0a4ed : 0xff6652a7),
        ],
      ).createShader(const Rect.fromLTWH(30, 0, 400, 440));
    for (double offset = 0; offset < 216; offset += 9) {
      canvas.drawPath(
        Path()
          ..moveTo(-30, 115 + offset)
          ..cubicTo(70, 15 + offset, 155, 260 + offset, 265, 145 + offset)
          ..cubicTo(375, 30 + offset, 430, 50 + offset, 510, 125 + offset),
        wave,
      );
    }
    canvas.restore();
    canvas.drawCircle(
      const Offset(230, 19),
      3.5,
      Paint()..color = const Color(0xff8c70b5),
    );
    final cross = Paint()
      ..color = const Color(0xffa38dbf)
      ..strokeWidth = .6;
    canvas.drawLine(const Offset(225, 230), const Offset(235, 230), cross);
    canvas.drawLine(const Offset(230, 225), const Offset(230, 235), cross);
    canvas.restore();
  }

  @override
  bool shouldRepaint(TideOrb oldDelegate) => dark != oldDelegate.dark;
}

class _Heading extends StatelessWidget {
  const _Heading(this.title, this.caption, this.link, this.open);
  final String title, caption, link;
  final VoidCallback open;
  @override
  Widget build(BuildContext context) {
    final d = WikiDesign(context);
    return Padding(
      padding: const EdgeInsets.only(top: 56, bottom: 23),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(caption, style: d.mono(9)),
                const SizedBox(height: 10),
                Text(title, style: d.kai(29)),
              ],
            ),
          ),
          TextButton(
            onPressed: open,
            child: Text(link, style: TextStyle(fontSize: 12, color: d.muted)),
          ),
        ],
      ),
    );
  }
}

class _Collection extends StatelessWidget {
  const _Collection({
    required this.category,
    required this.index,
    required this.open,
  });
  final Category category;
  final int index;
  final VoidCallback open;
  @override
  Widget build(BuildContext context) {
    final d = WikiDesign(context);
    final description = switch (category.name) {
      'Backend' => '走进系统背后的逻辑',
      'Frontend' => '让想法成为可见的体验',
      '知识导览' => '连起线索，找到学习路径',
      _ => '记录、整理与深入探索',
    };
    return Material(
      color: d.surface.withValues(alpha: d.dark ? .5 : .45),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(7),
        side: BorderSide(color: d.line),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: open,
        hoverColor: d.surface.withValues(alpha: .6),
        child: Padding(
          padding: const EdgeInsets.all(25),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(index.toString().padLeft(2, '0'), style: d.mono(11)),
                  Icon(Icons.north_east, size: 22, color: d.accent),
                ],
              ),
              const SizedBox(height: 23),
              Text(category.name, style: d.kai(29)),
              const SizedBox(height: 10),
              Text(description, style: TextStyle(fontSize: 12, color: d.muted)),
              const SizedBox(height: 24),
              Divider(color: d.line, height: 1),
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    '${category.total} 篇笔记',
                    style: TextStyle(fontSize: 10, color: d.muted),
                  ),
                  Text(
                    '打开目录 →',
                    style: TextStyle(fontSize: 10, color: d.accent),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NoteRow extends StatelessWidget {
  const _NoteRow({required this.note, required this.index, required this.open});
  final Note note;
  final int index;
  final VoidCallback open;
  @override
  Widget build(BuildContext context) {
    final d = WikiDesign(context);
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: open,
        hoverColor: d.hover,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 23),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: d.line)),
          ),
          child: Row(
            children: [
              Text(index.toString().padLeft(2, '0'), style: d.mono(11)),
              const SizedBox(width: 26),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      note.category,
                      style: TextStyle(fontSize: 10, color: d.muted),
                    ),
                    const SizedBox(height: 7),
                    Text(
                      note.title,
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w500,
                        height: 1.5,
                        color: d.ink,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Icon(Icons.north_east, size: 22, color: d.accent),
              const SizedBox(width: 14),
            ],
          ),
        ),
      ),
    );
  }
}
