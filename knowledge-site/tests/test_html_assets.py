import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app  # noqa: E402


class HTMLAssetTests(unittest.TestCase):
    def test_html_images_use_authorized_asset_route_and_styles_pass_csp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = root / "content"
            (content / "Backend").mkdir(parents=True)
            (content / "Private").mkdir()
            (content / "Backend/image.png").write_bytes(b"public-image")
            (content / "Private/image.png").write_bytes(b"private-image")
            db_path = root / "site.db"
            app = create_app(db_path, content)
            with sqlite3.connect(db_path) as db:
                for note_id, path, visibility, body in [
                    (1, "Backend/test.md", "public", '<span style="color:#FF6347">概念</span><img src="image.png"><img src="../Private/image.png">'),
                    (2, "Private/test.md", "private", '<img src="image.png">'),
                ]:
                    db.execute("INSERT INTO notes(id,path,title,category,visibility,body,body_sha,updated_at) VALUES(?,?,?,?,?,?,?,?)", (note_id, path, "测试", "测试", visibility, body, "test", 0))
            db.close()
            with TestClient(app) as client:
                response = client.get("/n/1")
                self.assertEqual(response.status_code, 200)
                self.assertIn('style="color:#ff6347"', response.text)
                self.assertIn('src="/asset/1?path=image.png"', response.text)
                self.assertNotIn('../Private/image.png', response.text)
                self.assertIn("style-src-attr 'unsafe-inline'", response.headers["Content-Security-Policy"])
                self.assertEqual(client.get("/asset/1?path=image.png").content, b"public-image")
                self.assertEqual(client.get("/asset/1?path=../Private/image.png").status_code, 404)
                self.assertEqual(client.get("/asset/2?path=image.png").status_code, 404)
                self.assertEqual(client.get("/n/2").status_code, 404)


if __name__ == "__main__":
    unittest.main()
