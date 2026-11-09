# Wikitide 服务端

FastAPI + SQLite 提供网页、账户、资料导入、团队协作、知识求索及客户端接口。网页模板和静态资源随源码提供，React 管理与编辑界面源文件位于 `team-ui/`。

## 独立运行

从仓库根目录按[项目说明](../README.md)安装依赖、复制示例内容并启动服务。默认读取本目录 `content/manifest.json` 与对应正文，数据库默认位于 `work/site.db`。空内容目录也可启动，管理员可通过网页创建笔记或导入资料。

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `KB_DB_PATH` | SQLite 数据库路径 | `work/site.db` |
| `KB_CONTENT_ROOT` | 内容及 manifest 目录 | `content/` |
| `KB_ALLOWED_HOSTS` | 允许的主机名，逗号分隔 | `localhost,127.0.0.1,testserver` |
| `KB_COOKIE_SECURE` | HTTPS 部署设为 `1` | `0` |
| `KB_SYNC_TOKEN_SHA256` | 同步令牌的 SHA-256 | 空值 |

生产运行需要反向代理、HTTPS、数据库备份和独立的管理员账户。`nginx-wikitide.conf` 与 `wikitide.service` 是部署示例，使用前调整域名、用户及目录。

## Docker

Compose 从本目录构建应用，在 `127.0.0.1:8766` 提供服务，挂载 `content/` 为只读目录，使用 `wikitide-data` 命名卷保存数据库。

```sh
cp .env.example .env
docker compose up --build -d
docker compose exec -it knowledge python scripts/admin_user.py \
  --db /data/site.db --content /app/content --username admin
```

上线前在 `.env` 中设置 `KB_COOKIE_SECURE=1` 与实际的 `KB_ALLOWED_HOSTS`。`docker compose down` 保留数据库卷。

## 连接自己的笔记库

应用代码和 Markdown 笔记库分别维护。内容打包工具接受显式的源目录，该目录需要包含 `categories.json`。它会导入公开分类、Wiki 导览及工具预设范围中的私密笔记，因此应先核对自己的导入范围。

```sh
python scripts/prepare_content.py \
  --source-root /absolute/path/to/your-notes \
  --target-root content
```

`categories.json` 与 `SCHEMA/wiki.py` 属于笔记库的可选集成。应用启动、网页导入及账户功能可独立使用。

双向同步配置保存在本目录被 Git 忽略的 `sync-config.json`，所有本地路径使用绝对路径。

```json
{
  "base_url": "https://your-site.example",
  "token": "YOUR_RANDOM_SYNC_TOKEN",
  "source_root": "/absolute/path/to/your-notes",
  "state_path": "/absolute/path/to/Wikitide/knowledge-site/work/sync-state.json",
  "conflict_root": "/absolute/path/to/Wikitide/knowledge-site/work/conflicts"
}
```

服务器配置对应令牌的 SHA-256，随后在本机运行。

```sh
python scripts/sync_local.py --config sync-config.json
```

程序按版本哈希比较内容，双方同时修改时保留冲突。自动发现公开笔记的目录规则在 `scripts/prepare_content.py` 与 `scripts/sync_v2.py` 中定义。使用新笔记结构前检查这些规则。

## 功能与验证

可选 OCR 和探索依赖分别位于 `requirements-import-ocr.txt` 与 `requirements-explore.txt`。AI 服务配置工具位于 `scripts/configure_explore.py`，模型和检索配置定义位于 `explore.py`。

从仓库根目录执行测试，启动脚本会将数据库和 AI 配置隔离到临时目录。

```sh
.venv/bin/python knowledge-site/scripts/run_agent_tests.py --pattern 'test_*.py'
```

`scripts/smoke_test.py` 面向已有完整内容库，包含固定内容数量及文章断言。独立仓库使用上述自包含测试。

每次实际发布应用变更时，在 `release-notes.json` 中新增用户可见说明，并运行 `python scripts/check_release_notes.py` 校验。部署已有站点前基于实际线上版本合并增量，保留现有主题和运行数据。
