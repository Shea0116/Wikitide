# Markdown 笔记库集成

`wiki.py` 提供 categories.json、Wiki 导览的生成与校验。同步集成测试会把该工具复制到临时笔记库，应用的网页功能可独立运行。

对自己的笔记库启用该集成时，将工具放到笔记库的 SCHEMA/ 下，先检查公开目录规则，再在笔记库根目录执行 `python3 SCHEMA/wiki.py check` 或 `python3 SCHEMA/wiki.py build`。
