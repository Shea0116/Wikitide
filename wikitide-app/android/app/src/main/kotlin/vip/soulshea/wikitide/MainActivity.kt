package vip.soulshea.wikitide

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import androidx.core.content.FileProvider
import android.content.Intent
import android.webkit.MimeTypeMap
import java.io.File

class MainActivity : FlutterActivity() {
    private var pendingSaveBytes: ByteArray? = null
    private var pendingSaveResult: MethodChannel.Result? = null
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val attachments = File(cacheDir, "wikitide-attachments")
        attachments.deleteRecursively()
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "wikitide/attachments")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "save" -> {
                        val bytes = call.argument<ByteArray>("bytes")
                        val name = call.argument<String>("name")
                        if (pendingSaveResult != null || bytes == null || name == null || bytes.size > 8 * 1024 * 1024) {
                            result.error("invalid", "草稿格式无效或正在保存", null)
                        } else {
                            pendingSaveBytes = bytes; pendingSaveResult = result
                            val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE)
                                type = "text/markdown"
                                putExtra(Intent.EXTRA_TITLE, File(name).name)
                            }
                            try { startActivityForResult(intent, 4107) }
                            catch (_: Exception) {
                                pendingSaveBytes = null; pendingSaveResult = null
                                result.error("save", "无法显示草稿保存选项", null)
                            }
                        }
                    }
                    "clear" -> { attachments.deleteRecursively(); result.success(null) }
                    "open" -> {
                        val bytes = call.argument<ByteArray>("bytes")
                        val name = call.argument<String>("name")
                        if (bytes == null || name == null || bytes.size > 4 * 1024 * 1024) {
                            result.error("invalid", "附件格式无效", null)
                        } else try {
                            val folder = File(attachments, java.util.UUID.randomUUID().toString())
                            folder.mkdirs()
                            val file = File(folder, File(name).name)
                            file.writeBytes(bytes)
                            val uri = FileProvider.getUriForFile(this, "$packageName.attachments", file)
                            val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(file.extension.lowercase()) ?: "application/octet-stream"
                            val intent = Intent(Intent.ACTION_VIEW).apply {
                                setDataAndType(uri, mime)
                                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                            }
                            startActivity(intent)
                            result.success(null)
                        } catch (_: android.content.ActivityNotFoundException) {
                            result.error("no_viewer", "请安装可以打开此附件的应用", null)
                        } catch (_: Exception) {
                            result.error("attachment", "附件无法打开", null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 4107) return
        val result = pendingSaveResult ?: return
        val bytes = pendingSaveBytes
        pendingSaveBytes = null; pendingSaveResult = null
        if (resultCode != RESULT_OK || data?.data == null || bytes == null) {
            result.success(false); return
        }
        try {
            val stream = contentResolver.openOutputStream(data.data!!)
                ?: throw IllegalStateException("No output stream")
            stream.use { it.write(bytes) }
            result.success(true)
        } catch (_: Exception) { result.error("save", "草稿无法保存", null) }
    }
}
