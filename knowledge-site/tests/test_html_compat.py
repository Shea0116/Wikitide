import sys
import unittest
from pathlib import Path

from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from html_compat import sanitize_html  # noqa: E402
from markdown_compat import enhance_markdown_tokens  # noqa: E402


class HTMLCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.markdown = MarkdownIt("default", {"html": True})

    def render(self, source):
        return sanitize_html(self.markdown.render(source))

    def test_safe_note_html_and_styles_are_rendered(self):
        html = self.render(
            '<span style="color:#FF6347;font-weight:700;">核心概念：</span>'
            '<div style="border-left:4px solid #DC143C;padding:0.55em 0.8em;">提示</div>'
            '<mark style="background-color:#FFFF99;color:#1F2937;">重点</mark><br>下一行'
        )
        self.assertIn('<span style="color:#ff6347;font-weight:700">核心概念：</span>', html)
        self.assertIn('<div style="border-left:4px solid #dc143c;padding:0.55em 0.8em">提示</div>', html)
        self.assertIn('<mark style="background-color:#ffff99;color:#1f2937">重点</mark>', html)
        self.assertIn("<br />", html)

    def test_markdown_structure_survives_sanitizing(self):
        html = self.render("# 标题\n\n- 项目\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertIn("<h1>标题</h1>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    def test_scripts_and_unsafe_attributes_are_removed(self):
        html = self.render(
            '<script>alert(1)</script><span onclick="alert(2)" style="color:red; background:url(https://evil.invalid/x);">安全文字</span>'
            '<a href="javascript:alert(3)">危险链接</a>'
        )
        self.assertNotIn("alert", html)
        self.assertIn('<span style="color:red">安全文字</span>', html)
        self.assertIn("危险链接", html)
        self.assertNotIn("javascript:", html)

    def test_separated_ordered_lists_keep_their_start_numbers(self):
        html = self.render(
            "1. **typeof**\n\n- 类型说明\n\n"
            "2. **instanceof**\n\n- 实例说明\n\n"
            "3. **根据原型判断**\n\n- 原型说明\n"
        )
        self.assertIn('<ol>\n<li><strong>typeof</strong></li>', html)
        self.assertIn('<ol start="2">\n<li><strong>instanceof</strong></li>', html)
        self.assertIn('<ol start="3">\n<li><strong>根据原型判断</strong></li>', html)

    def test_ordered_list_start_accepts_only_integers(self):
        for value in ("0", "-2", "12"):
            with self.subTest(value=value):
                html = sanitize_html(f'<ol start="{value}" onclick="alert(1)"><li>项目</li></ol>')
                self.assertEqual(html, f'<ol start="{value}"><li>项目</li></ol>')
        for value in ("", "2.5", "two", "2; color:red", "javascript:alert(1)"):
            with self.subTest(value=value):
                html = sanitize_html(f'<ol start="{value}"><li>项目</li></ol>')
                self.assertEqual(html, '<ol><li>项目</li></ol>')

    def test_task_checkbox_is_read_only(self):
        tokens = enhance_markdown_tokens(self.markdown.parse("- [x] 已完成"))
        html = sanitize_html(self.markdown.renderer.render(tokens, self.markdown.options, {}))
        self.assertIn('<input class="task-checkbox" type="checkbox" disabled checked aria-label="已完成" />', html)

    def test_forbidden_void_and_self_closing_elements_preserve_following_text(self):
        html = sanitize_html('<embed src="x"><iframe />之后<span />正文')
        self.assertEqual(html, '之后<span></span>正文')

    def test_unclosed_allowed_tags_are_closed_inside_note(self):
        self.assertEqual(sanitize_html('<div><span>内容'), '<div><span>内容</span></div>')

    def test_obfuscated_script_urls_are_removed(self):
        html = sanitize_html('<a href="java&#10;script:alert(1)">一</a><img src="//evil.invalid/x"><a href="javascript&colon;alert(2)">二</a>')
        self.assertNotIn('href=', html)
        self.assertNotIn('src=', html)

    def test_code_examples_remain_literal(self):
        html = self.render('```html\n<span style="color:red">示例</span>\n```')
        self.assertIn('&lt;span style=&quot;color:red&quot;&gt;示例&lt;/span&gt;', html)
        self.assertNotIn('<span', html)


if __name__ == "__main__":
    unittest.main()
