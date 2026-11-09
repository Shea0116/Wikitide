# 字体与资源

- `assets/logo.png` 沿用 WikiTide 网页项目中的 Logo。
- `assets/fonts/WikiTideKai.ttf` 由原网页使用的 97 个霞鹜文楷 WOFF2 子集合并而成，保留原字体字形，不修改原网页字体文件。许可见 [OFL-LXGWWenKai.txt](assets/fonts/OFL-LXGWWenKai.txt)。
- `assets/fonts/WikiTideSong.ttf` 是 Google Fonts 提供的 Noto Serif SC 400 标题字符子集，移动端使用；macOS 主标题优先使用系统宋体。许可见 [OFL-NotoSerifSC.txt](assets/fonts/OFL-NotoSerifSC.txt)。
- 潮汐图由 Flutter `CustomPainter` 按原网页的 SVG 曲线、轨道和配色重绘。

Flutter 依赖及版本见 `pubspec.lock`。这些依赖的许可可在 Flutter 的 LicensePage 中查看。
