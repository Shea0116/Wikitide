"""Bounded, presentation-neutral Git-style diffs shared by personal and team history."""
import difflib
from itertools import zip_longest

from fastapi import HTTPException


def segments(before, after):
    if len(before) + len(after) > 4000:
        return [{'text': before, 'changed': bool(before)}], [{'text': after, 'changed': bool(after)}]
    left, right = [], []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, before, after).get_opcodes():
        if a != b:
            left.append({'text': before[a:b], 'changed': tag != 'equal'})
        if c != d:
            right.append({'text': after[c:d], 'changed': tag != 'equal'})
    return left, right


def make_diff(before, after, old_label='旧版本', new_label='新版本', context=3):
    old, new = before.splitlines(), after.splitlines()
    if len(before) + len(after) > 1_000_000 or len(old) + len(new) > 12000:
        raise HTTPException(413, '版本过大，暂不支持在线差异比较，请下载原文后比较')
    matcher = difflib.SequenceMatcher(None, old, new)
    added = deleted = 0
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag in {'insert', 'replace'}:
            added += d - c
        if tag in {'delete', 'replace'}:
            deleted += b - a
    hunks = []
    inline_budget = 200
    for group in matcher.get_grouped_opcodes(context):
        rows, unified = [], []
        for tag, a, b, c, d in group:
            for i, j in zip_longest(range(a, b), range(c, d)):
                x, y = old[i] if i is not None else '', new[j] if j is not None else ''
                if tag == 'replace' and inline_budget:
                    left, right = segments(x, y)
                    inline_budget -= 1
                else:
                    left, right = [{'text': x, 'changed': False}], [{'text': y, 'changed': False}]
                rows.append({'kind': tag, 'old_no': i + 1 if i is not None else None,
                             'new_no': j + 1 if j is not None else None, 'old': left, 'new': right})
            if tag in {'equal', 'delete', 'replace'}:
                unified.extend({'kind': 'equal' if tag == 'equal' else 'delete', 'old_no': i + 1,
                                'new_no': c + i - a + 1 if tag == 'equal' else None,
                                'text': old[i], 'sign': ' ' if tag == 'equal' else '-'} for i in range(a, b))
            if tag in {'insert', 'replace'}:
                unified.extend({'kind': 'insert', 'old_no': None, 'new_no': j + 1,
                                'text': new[j], 'sign': '+'} for j in range(c, d))
        first, last = group[0], group[-1]
        def span(start, end):
            length = end - start
            return f'{start},0' if length == 0 else str(start+1) if length == 1 else f'{start+1},{length}'
        header = f'@@ -{span(first[1],last[2])} +{span(first[3],last[4])} @@'
        hunks.append({'header': header, 'split': rows, 'unified': unified})
    patch = ''.join(line if line.endswith('\n') else line+'\n\\ No newline at end of file\n'
                    for line in difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                                    fromfile=old_label, tofile=new_label))
    # A final newline matters even when splitlines() yields identical display rows.
    newline_changed = before.endswith('\n') != after.endswith('\n')
    return {'old_label': old_label, 'new_label': new_label, 'added': added, 'deleted': deleted,
            'hunks': hunks, 'patch': patch, 'identical': before == after,
            'newline_changed': newline_changed}
