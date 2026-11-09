import sys
import unittest
from pathlib import Path

from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from markdown_compat import enhance_markdown_tokens  # noqa: E402


class MarkdownCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.markdown = MarkdownIt("default", {"html": False})

    def render(self, source):
        tokens = enhance_markdown_tokens(self.markdown.parse(source))
        return self.markdown.renderer.render(tokens, self.markdown.options, {})

    def test_note_bold_label_and_merged_table_cells(self):
        source = """**注意：**枚举可以单独声明。

-tx-
| 类别 | 关键字 |
| --- | --- |
| 访问控制 | private |
| ^^ | protected |
| ^^ | public |
| 程序控制 | break |
| ^^ | continue |
"""
        html = self.render(source)
        self.assertIn("<strong>注意：</strong>枚举", html)
        self.assertIn('<td rowspan="3">访问控制</td>', html)
        self.assertIn('<td rowspan="2">程序控制</td>', html)
        self.assertNotIn("-tx-", html)
        self.assertNotIn("^^", html)

    def test_code_and_other_text_remain_literal(self):
        html = self.render("`**注意：**枚举`\n\n\\*\\*注意：\\*\\*枚举\n\n-tx-\n")
        self.assertIn("<code>**注意：**枚举</code>", html)
        self.assertIn("<p>**注意：**枚举</p>", html)
        self.assertIn("<p>-tx-</p>", html)

    def test_raw_html_stays_escaped(self):
        html = self.render("**注意：**<script>alert(1)</script>后文")
        self.assertNotIn("<script>", html)

    def test_task_lists_render_read_only_checkboxes(self):
        html = self.render("- [ ] 待完成\n- [x] 已完成\n")
        self.assertIn('type="checkbox" disabled aria-label="未完成"', html)
        self.assertIn('type="checkbox" disabled checked aria-label="已完成"', html)
        self.assertNotIn("[ ] 待完成", html)


if __name__ == "__main__":
    unittest.main()
