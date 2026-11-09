# 识汐 Wikitide

面向个人学习与团队协作的知识库应用。整理 Markdown 与 PDF 资料，关联阅读、检索、问答、写作和复习，让知识逐步积累。

[体验网站](https://wikitide.soulshea.vip) · [macOS 下载](https://wikitide.soulshea.vip/static/updates/macos/WikiTide-0.5.0-macOS.zip) · [客户端说明](wikitide-app/README.md)

## 功能

- **阅读与整理** 支持分类、关系图谱、双向链接、收藏、阅读进度和文内查找。
- **编辑与导入** 提供 Markdown 实时预览、版本对比、草稿恢复，以及 Markdown、PDF、DOCX 导入。
- **知识求索** 支持带来源引用的问答、联网检索、文档写作、学习反馈与后台研究任务。
- **团队协作** 支持团队空间、成员角色、讨论、审阅、权限管理和链接分享。
- **多端使用** 网站提供中英文界面，Flutter 客户端覆盖 macOS、Android 和 iOS 工程。
- **本地连接** 通过显式配置连接自己的 Markdown 笔记库，支持双向同步与冲突处理。

AI、联网搜索和 OCR 功能需要配置服务或可选依赖。macOS 提供已发布的安装包，Android 与 iOS 可从源码构建，移动端真机兼容性仍需验证。

## 代码结构

```text
knowledge-site/          FastAPI、SQLite、网页模板与静态资源
  team-ui/              React + TypeScript 团队、管理与编辑界面
  scripts/              同步、管理和校验工具
  tests/                服务端测试
wikitide-app/            Flutter 客户端与各平台工程
examples/demo-content/  独立运行使用的示例内容
SCHEMA/wiki.py          可选的 Markdown 笔记库分类集成工具
```

## 本地运行

需要 Python 3.13。下面的命令从仓库根目录执行。

```sh
git clone https://github.com/Shea0116/Wikitide.git
cd Wikitide
python3 -m venv .venv
.venv/bin/python -m pip install -r knowledge-site/requirements.txt
cp -R examples/demo-content knowledge-site/content
cd knowledge-site
../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>。示例提供一篇公开欢迎文章，运行数据保存在 `knowledge-site/work/`。需要管理功能时，在另一个终端进入仓库根目录执行，按提示设置密码。

```sh
.venv/bin/python knowledge-site/scripts/admin_user.py \
  --db knowledge-site/work/site.db \
  --content knowledge-site/content \
  --username admin
```

也可使用 Docker Compose。完成示例内容复制后执行。

```sh
cd knowledge-site
cp .env.example .env
docker compose up --build -d
```

Docker 入口为 <http://127.0.0.1:8766>，数据库持久化到命名卷。管理员命令见[服务端说明](knowledge-site/README.md)。

## 开发与验证

```sh
# 在仓库根目录运行，测试使用临时数据库
.venv/bin/python knowledge-site/scripts/run_agent_tests.py --pattern 'test_*.py'

# 编译 React 与编辑器资源，需要 Node.js 20.19+ 或 22.12+
cd knowledge-site/team-ui
npm ci
npm test
npm run build

# Flutter 客户端，在 wikitide-app 目录运行
flutter pub get
flutter analyze
flutter test
```

客户端默认连接体验网站。自建站点可使用 `--dart-define=WIKITIDE_SERVER=https://your-site.example`，构建、签名及发布步骤见[客户端说明](wikitide-app/README.md)。

## 自托管与说明

- [服务端运行、数据目录与同步](knowledge-site/README.md)
- [客户端构建与站点配置](wikitide-app/README.md)
- [字体及第三方许可](wikitide-app/THIRD_PARTY_NOTICES.md)

部署时设置自己的域名、HTTPS、AI 服务和备份。笔记正文、数据库、同步配置与签名材料放在 Git 忽略的本地目录。公开仓库随附独立示例内容，实际笔记库由使用者单独维护。
