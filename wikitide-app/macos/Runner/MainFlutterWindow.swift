import Cocoa
import FlutterMacOS

class MainFlutterWindow: NSWindow {
  private var attachmentChannel: FlutterMethodChannel?
  private var desktopChannel: FlutterMethodChannel?
  private let attachments = FileManager.default.temporaryDirectory.appendingPathComponent("WikiTideAttachments", isDirectory: true)
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setFrame(windowFrame, display: true)

    RegisterGeneratedPlugins(registry: flutterViewController)
    desktopChannel = FlutterMethodChannel(name: "wikitide/desktop", binaryMessenger: flutterViewController.engine.binaryMessenger)
    desktopChannel?.setMethodCallHandler { call, result in
      guard call.method == "checkForUpdates" else { result(FlutterMethodNotImplemented); return }
#if APP_STORE
      result(FlutterMethodNotImplemented)
#else
      guard let app = NSApp.delegate as? AppDelegate else {
        result(FlutterError(code: "updater", message: "更新服务尚未准备好，请稍后重试。", details: nil)); return
      }
      guard app.canCheckForUpdates else {
        result(FlutterError(code: "busy", message: "正在检查或安装更新，请查看更新窗口。", details: nil)); return
      }
      app.checkForUpdates(nil)
      result(nil)
#endif
    }

    try? FileManager.default.removeItem(at: attachments)
    attachmentChannel = FlutterMethodChannel(name: "wikitide/attachments", binaryMessenger: flutterViewController.engine.binaryMessenger)
    attachmentChannel?.setMethodCallHandler { [weak self] call, result in
      guard let self = self else { return }
      if call.method == "clear" {
        try? FileManager.default.removeItem(at: self.attachments)
        result(nil)
        return
      }
      if call.method == "readMemoryImport" {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedFileTypes = ["json"]
        panel.beginSheetModal(for: self) { response in
          guard response == .OK, let url = panel.url else { result(nil); return }
          let scoped = url.startAccessingSecurityScopedResource()
          defer { if scoped { url.stopAccessingSecurityScopedResource() } }
          do {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            guard let size = attributes[.size] as? NSNumber, size.intValue <= 8 * 1024 * 1024 else {
              result(FlutterError(code: "large", message: "导入文件超过 8 MiB", details: nil)); return
            }
            let data = try Data(contentsOf: url, options: .mappedIfSafe)
            guard data.count <= 8 * 1024 * 1024, let text = String(data: data, encoding: .utf8) else {
              result(FlutterError(code: "invalid", message: "请选择 UTF-8 JSON 文件", details: nil)); return
            }
            result(text)
          } catch {
            result(FlutterError(code: "read", message: "记忆文件无法读取", details: nil))
          }
        }
        return
      }
      if call.method == "save" {
        guard let args = call.arguments as? [String: Any],
              let bytes = args["bytes"] as? FlutterStandardTypedData,
              let name = args["name"] as? String, bytes.data.count <= 8 * 1024 * 1024 else {
          result(FlutterError(code: "invalid", message: "草稿格式无效", details: nil)); return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = URL(fileURLWithPath: name).lastPathComponent
        panel.beginSheetModal(for: self) { response in
          guard response == .OK, let url = panel.url else { result(false); return }
          do { try bytes.data.write(to: url, options: .atomic); result(true) }
          catch { result(FlutterError(code: "save", message: "草稿无法保存", details: nil)) }
        }
        return
      }
      guard call.method == "open", let args = call.arguments as? [String: Any],
            let bytes = args["bytes"] as? FlutterStandardTypedData,
            let name = args["name"] as? String,
            bytes.data.count <= 4 * 1024 * 1024 else {
        result(FlutterError(code: "invalid", message: "附件格式无效", details: nil)); return
      }
      let safeName = URL(fileURLWithPath: name).lastPathComponent
      do {
        try FileManager.default.createDirectory(at: self.attachments, withIntermediateDirectories: true)
        let folder = self.attachments.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(safeName)
        try bytes.data.write(to: file, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        if NSWorkspace.shared.open(file) { result(nil) }
        else { result(FlutterError(code: "no_viewer", message: "没有可以打开此附件的应用", details: nil)) }
      } catch {
        result(FlutterError(code: "attachment", message: "附件无法打开", details: nil))
      }
    }

    self.minSize = NSSize(width: 760, height: 620)
    self.setContentSize(NSSize(width: 1280, height: 820))
    self.styleMask.insert(.fullSizeContentView)
    self.titlebarAppearsTransparent = true
    self.titleVisibility = .hidden
    self.backgroundColor = NSColor(srgbRed: 0xf1 / 255.0, green: 0xeb / 255.0, blue: 0xdf / 255.0, alpha: 1)

    super.awakeFromNib()
    if let viewMenu = NSApp.mainMenu?.item(withTitle: "View")?.submenu {
      viewMenu.addItem(.separator())
      let sidebarItem = NSMenuItem(title: "显示或隐藏侧边栏", action: #selector(toggleSidebarFromMenu(_:)), keyEquivalent: "b")
      sidebarItem.keyEquivalentModifierMask = .command
      sidebarItem.target = self
      viewMenu.addItem(sidebarItem)
      let searchItem = NSMenuItem(title: "搜索笔记", action: #selector(focusSearchFromMenu(_:)), keyEquivalent: "k")
      searchItem.keyEquivalentModifierMask = .command
      searchItem.target = self
      viewMenu.addItem(searchItem)
    }
    // Retarget the standard Find menu so WKWebView cannot consume these
    // shortcuts with its own page-wide find panel when it has keyboard focus.
    if let findMenu = NSApp.mainMenu?.item(withTitle: "Edit")?.submenu?.item(withTitle: "Find")?.submenu {
      if let item = findMenu.item(withTag: 1) {
        item.title = "文内查找"
        item.target = self
        item.action = #selector(findInArticleFromMenu(_:))
      }
      if let item = findMenu.item(withTag: 2) {
        item.title = "查找下一个"
        item.target = self
        item.action = #selector(findNextFromMenu(_:))
      }
      if let item = findMenu.item(withTag: 3) {
        item.title = "查找上一个"
        item.target = self
        item.action = #selector(findPreviousFromMenu(_:))
        item.keyEquivalent = "g"
        item.keyEquivalentModifierMask = [.command, .shift]
      }
    }
  }

  @objc private func toggleSidebarFromMenu(_ sender: NSMenuItem) {
    desktopChannel?.invokeMethod("toggleSidebar", arguments: nil)
  }

  func prepareForUpdate(_ completion: @escaping (Bool) -> Void) {
    desktopChannel?.invokeMethod("prepareForUpdate", arguments: nil) { result in
      completion(result as? Bool == true)
    }
  }

  @objc private func focusSearchFromMenu(_ sender: NSMenuItem) {
    desktopChannel?.invokeMethod("focusSearch", arguments: nil)
  }

  @objc private func findInArticleFromMenu(_ sender: NSMenuItem) {
    desktopChannel?.invokeMethod("findInArticle", arguments: nil)
  }

  @objc private func findNextFromMenu(_ sender: NSMenuItem) {
    desktopChannel?.invokeMethod("findNext", arguments: nil)
  }

  @objc private func findPreviousFromMenu(_ sender: NSMenuItem) {
    desktopChannel?.invokeMethod("findPrevious", arguments: nil)
  }
}
