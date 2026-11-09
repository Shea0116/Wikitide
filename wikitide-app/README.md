# 识汐 Wikitide 客户端

Flutter 客户端工程覆盖 macOS、Android 和 iOS。macOS 提供原生侧栏、文内查找、在线更新、离线阅读及本机加密记忆，网页内容由连接的 Wikitide 服务提供。

[体验网站](https://wikitide.soulshea.vip) · [macOS 0.5.0 下载](https://wikitide.soulshea.vip/static/updates/macos/WikiTide-0.5.0-macOS.zip)

## 开发

在本目录执行，需要 Flutter SDK 和目标平台的开发工具。

```sh
flutter pub get
flutter analyze
flutter test
```

默认连接体验网站。开发或自托管时通过构建参数指定自己的服务地址。

```sh
flutter run --dart-define=WIKITIDE_SERVER=https://your-site.example
```

## 构建

macOS 构建前下载经校验的 Sparkle 更新框架。

```sh
python3 scripts/setup_macos_updater.py
flutter build macos --release
```

移动端工程可按各自平台的要求构建。

```sh
python3 scripts/build_android.py
flutter build ios --release --no-codesign
flutter build ios --simulator --debug
```

Android 正式发行需配置自己的签名密钥。iOS 真机安装需在 Xcode 中配置自己的开发团队、证书和 provisioning profile。移动端真机兼容性仍需验证。

## macOS 发行与更新

应用使用 Sparkle 验证更新列表和安装包，发行工具位于 `scripts/release_macos.py` 与 `scripts/publish_macos_updates.py`，版本说明位于 `release-notes/`。

构建自有发行版时，设置自己的更新地址、签名公钥和私钥，并完成 Apple Developer ID 签名与公证。当前下载包使用本机测试签名。签名私钥保存在 Git 忽略的本机目录，已发行版本对应的私钥需要独立备份。

## 资源与许可

图标和阅读样式位于 `assets/`，字体及第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
