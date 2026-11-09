import Flutter
import UIKit
import QuickLook

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate, QLPreviewControllerDataSource {
  private var attachmentChannel: FlutterMethodChannel?
  private var previewURL: URL?
  private let attachments = FileManager.default.temporaryDirectory.appendingPathComponent("WikiTideAttachments", isDirectory: true)
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    try? FileManager.default.removeItem(at: attachments)
    attachmentChannel = FlutterMethodChannel(name: "wikitide/attachments", binaryMessenger: engineBridge.applicationRegistrar.messenger())
    attachmentChannel?.setMethodCallHandler { [weak self] call, result in
      guard let self = self else { return }
      if call.method == "clear" {
        self.previewURL = nil
        try? FileManager.default.removeItem(at: self.attachments)
        result(nil); return
      }
      if call.method == "save" {
        guard let args = call.arguments as? [String: Any],
              let bytes = args["bytes"] as? FlutterStandardTypedData,
              let name = args["name"] as? String, bytes.data.count <= 8 * 1024 * 1024 else {
          result(FlutterError(code: "invalid", message: "草稿格式无效", details: nil)); return
        }
        do {
          let folder = self.attachments.appendingPathComponent(UUID().uuidString, isDirectory: true)
          try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
          let file = folder.appendingPathComponent(URL(fileURLWithPath: name).lastPathComponent)
          try bytes.data.write(to: file, options: [.atomic, .completeFileProtection])
          let sheet = UIActivityViewController(activityItems: [file], applicationActivities: nil)
          var presenter = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }.first { $0.isKeyWindow }?.rootViewController
          while let presented = presenter?.presentedViewController { presenter = presented }
          guard let presenter = presenter else {
            result(FlutterError(code: "save", message: "无法显示草稿保存选项", details: nil)); return
          }
          sheet.popoverPresentationController?.sourceView = presenter.view
          sheet.popoverPresentationController?.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY, width: 1, height: 1)
          sheet.completionWithItemsHandler = { _, completed, _, _ in result(completed) }
          presenter.present(sheet, animated: true)
        } catch { result(FlutterError(code: "save", message: "草稿无法保存", details: nil)) }
        return
      }
      guard call.method == "open", let args = call.arguments as? [String: Any],
            let bytes = args["bytes"] as? FlutterStandardTypedData,
            let name = args["name"] as? String,
            bytes.data.count <= 4 * 1024 * 1024 else {
        result(FlutterError(code: "invalid", message: "附件格式无效", details: nil)); return
      }
      do {
        let folder = self.attachments.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(URL(fileURLWithPath: name).lastPathComponent)
        try bytes.data.write(to: file, options: [.atomic, .completeFileProtection])
        self.previewURL = file
        let preview = QLPreviewController()
        preview.dataSource = self
        var presenter = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
          .flatMap { $0.windows }.first { $0.isKeyWindow }?.rootViewController
        while let presented = presenter?.presentedViewController { presenter = presented }
        guard let presenter = presenter else {
          result(FlutterError(code: "no_viewer", message: "无法显示附件预览", details: nil)); return
        }
        presenter.present(preview, animated: true)
        result(nil)
      } catch {
        result(FlutterError(code: "attachment", message: "附件无法打开", details: nil))
      }
    }
  }

  func numberOfPreviewItems(in controller: QLPreviewController) -> Int { previewURL == nil ? 0 : 1 }
  func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem {
    return previewURL! as NSURL
  }
}
