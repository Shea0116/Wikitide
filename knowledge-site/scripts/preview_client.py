"""Run a loopback-only native-client preview with disposable example content."""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=BASE / 'work/client-preview')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    root = args.root.resolve()
    marker = root / '.wikitide-client-preview'
    if (root / 'site.db').exists() and not marker.exists():
        parser.error('Refusing to reuse a database not created by this preview')
    root.mkdir(parents=True, exist_ok=True)
    marker.touch()
    content = root / 'content'
    content.mkdir(exist_ok=True)
    os.environ['KB_DB_PATH'] = str(root / 'site.db')
    os.environ['KB_CONTENT_ROOT'] = str(content)
    os.environ['KB_ALLOWED_HOSTS'] = '127.0.0.1,localhost'
    from app import app
    from storage import connect, create_user
    from features import add_note
    with connect(root / 'site.db') as db:
        if not db.execute('SELECT 1 FROM users').fetchone():
            create_user(db, 'preview', 'preview-password-123', 'admin')
        if not db.execute('SELECT 1 FROM notes').fetchone():
            body = '''## 让知识有迹可循

这里是客户端验证用的示例内容，不是原始笔记。

## <span style="color:#DC143C;font-weight:bold;">跨端阅读需要注意什么？</span>

<span style="color:#0070C0;font-weight:bold;">账户权限</span>、正文颜色与历史版本由同一台服务器管理。

```dart
final platforms = ['macOS', 'Android', 'iOS'];
print(platforms.join(' · '));
```

| 平台 | 第一版 |
| --- | --- |
| macOS | 分类、搜索与阅读 |
| Android | 相同账户与正文 |
| iOS | 相同账户与正文 |

![示例波纹](static/tide.svg)

[[版本与草稿|继续了解版本与草稿]]

## 自测清单

- [x] 彩色标题与中文正文
- [x] 表格和代码复制
- [ ] 在第二台设备继续阅读
'''
            add_note(db, 'Frontend/跨端阅读.md', body, summary='用于验证彩色正文、表格、代码、图片和双链。', category='Frontend / Flutter', visibility='public')
            add_note(db, 'Frontend/版本与草稿.md', '## 本机草稿\n\n编辑时自动保存本机草稿。\n\n## 版本冲突\n\n两端同时修改时，客户端保留输入并要求合并。\n\n' + ('长文阅读测试。可以滚动正文、查看目录和同步阅读进度。\n\n' * 40), summary='验证编辑、历史恢复与阅读进度。', category='Frontend / Flutter', visibility='public')
            add_note(db, 'Backend/账户权限.md', '## 账户权限\n\n公开内容可匿名阅读；私密内容由服务器逐篇授权。', summary='验证分类导航与搜索。', category='Backend', visibility='public')
            add_note(db, 'Developer/私密示例.md', '## 仅用于测试\n\n这是虚构的私密内容，不包含用户资料。', category='私密示例', visibility='private')
    asset = content / 'Frontend/static/tide.svg'
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="140"><rect width="640" height="140" rx="18" fill="#f6f3fc"/><path d="M0 65 Q160 0 320 65 T640 65 M0 90 Q160 25 320 90 T640 90" fill="none" stroke="#7653ad" stroke-width="2"/></svg>')
    print(f'Client preview: http://127.0.0.1:{args.port} (example content only)', flush=True)
    print('Demo account: preview / preview-password-123', flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
