import Cocoa
import FlutterMacOS
#if !APP_STORE
import Sparkle
#endif

@main
class AppDelegate: FlutterAppDelegate {
#if !APP_STORE
  private var pendingInstallation: (() -> Void)?
  private(set) lazy var updaterController = SPUStandardUpdaterController(
    startingUpdater: true, updaterDelegate: self, userDriverDelegate: nil)

  override func applicationDidFinishLaunching(_ notification: Notification) {
    super.applicationDidFinishLaunching(notification)
    _ = updaterController
  }

  var canCheckForUpdates: Bool {
    pendingInstallation != nil || updaterController.updater.canCheckForUpdates
  }

  @objc func checkForUpdates(_ sender: Any?) {
    if pendingInstallation != nil { continueInstallationWhenReady() }
    else { updaterController.checkForUpdates(sender) }
  }

  func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
    menuItem.action == #selector(checkForUpdates(_:)) ? canCheckForUpdates : true
  }

  func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
               untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
    pendingInstallation = installHandler
    DispatchQueue.main.async { [weak self] in self?.continueInstallationWhenReady() }
    return true
  }

  private func continueInstallationWhenReady() {
    guard let window = mainFlutterWindow as? MainFlutterWindow else { return }
    window.prepareForUpdate { [weak self] ready in
      guard let self = self, let install = self.pendingInstallation else { return }
      if ready {
        self.pendingInstallation = nil
        install()
      } else {
        let alert = NSAlert()
        alert.messageText = "请先保存当前内容"
        alert.informativeText = "当前页面仍有未提交的修改，或正在生成、保存文稿。更新已暂停；完成保存后，再点击“检查更新”即可继续安装。"
        alert.addButton(withTitle: "返回保存")
        alert.beginSheetModal(for: window)
      }
    }
  }
#endif

  override func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    return true
  }

  override func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
    return true
  }
}

#if !APP_STORE
extension AppDelegate: SPUUpdaterDelegate, NSMenuItemValidation {}
#endif
