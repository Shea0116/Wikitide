"""Exercise the injected find code in a real browser (requires agent-browser)."""

from pathlib import Path
import subprocess


root = Path(__file__).resolve().parents[1]
source = (root / "lib/article_find.dart").read_text().split("r'''", 1)[1].rsplit("''';", 1)[0]
tests = (root / "test/article_find_browser_test.js").read_text()
browser = ["rtk", "agent-browser", "--session", "wikitide-article-find-test"]
try:
    subprocess.run([*browser, "open", "about:blank"], check=True)
    subprocess.run([*browser, "eval", "--stdin"], input=source + "\n" + tests, text=True, check=True)
finally:
    subprocess.run([*browser, "close"], check=False)
