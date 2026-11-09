import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../api.dart';
import '../models.dart';

class AgentStreamEvent {
  const AgentStreamEvent(this.type, this.data);
  final String type;
  final Json data;
}

/// A bounded SSE parser accepts chunked UTF-8 and multi-line data. Completion
/// requires an explicit done event; an interrupted stream is never saved as a
/// completed answer.
Stream<AgentStreamEvent> decodeAgentEvents(Stream<List<int>> stream) async* {
  String type = 'message';
  final data = <String>[];
  var bytes = 0;
  await for (final line
      in stream.transform(utf8.decoder).transform(const LineSplitter())) {
    bytes += utf8.encode(line).length;
    if (bytes > 2 * 1024 * 1024) throw const ApiException('回答超过客户端大小限制');
    if (line.isEmpty) {
      if (data.isNotEmpty) {
        dynamic decoded;
        try {
          decoded = jsonDecode(data.join('\n'));
        } on FormatException {
          throw const ApiException('服务器返回的流式事件格式无效');
        }
        if (decoded is! Map) throw const ApiException('流式事件内容无效');
        yield AgentStreamEvent(type, Map<String, dynamic>.from(decoded));
      }
      type = 'message';
      data.clear();
      continue;
    }
    if (line.startsWith('event:')) type = line.substring(6).trim();
    if (line.startsWith('data:')) data.add(line.substring(5).trimLeft());
  }
  if (data.isNotEmpty) throw const ApiException('连接提前结束，回答尚未完成');
}

class AgentTransientApi {
  AgentTransientApi(this.api, {http.Client Function()? clientFactory})
    : _factory = clientFactory ?? http.Client.new;
  final WikiApi api;
  final http.Client Function() _factory;
  http.Client? _active;

  void cancel() {
    _active?.close();
    _active = null;
  }

  static void validateRequest(Json body) {
    if (utf8.encode(jsonEncode(body)).length > 64 * 1024) {
      throw const ApiException('本轮上下文超过 64 KiB，请减少待发送记忆或清空对话');
    }
    final history = body['history'];
    final memories = body['memory_context'];
    if (history is! List ||
        history.length > 12 ||
        memories is! List ||
        memories.length > 5) {
      throw const ApiException('历史最多 12 条，待发送记忆最多 5 条');
    }
    for (final row in history) {
      if (row is! Map ||
          !{'user', 'assistant'}.contains(row['role']) ||
          row['content'] is! String) {
        throw const ApiException('对话角色或内容格式无效');
      }
    }
  }

  Stream<AgentStreamEvent> ask(Json body) async* {
    validateRequest(body);
    final token = api.token;
    if (token == null) throw const ApiException('请先验证原生账户');
    final client = _factory();
    _active = client;
    final request =
        http.Request('POST', api.url('$apiPrefix/agent/transient/ask/stream'))
          ..followRedirects = false
          ..headers.addAll({
            'Authorization': 'Bearer $token',
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
          })
          ..body = jsonEncode(body);
    try {
      final response = await client
          .send(request)
          .timeout(const Duration(seconds: 30));
      if (response.statusCode != 200) {
        await response.stream.drain<void>();
        if (response.statusCode == 401) api.onExpired?.call();
        throw ApiException(
          response.statusCode == 503
              ? '服务器尚未启用本机临时问答'
              : '临时问答失败（${response.statusCode}）',
          status: response.statusCode,
        );
      }
      if (!(response.headers['content-type'] ?? '').contains(
        'text/event-stream',
      )) {
        throw const ApiException('服务器未返回流式回答');
      }
      var completed = false;
      await for (final event in decodeAgentEvents(
        response.stream.timeout(const Duration(seconds: 90)),
      )) {
        if (event.type == 'error') {
          throw ApiException(
            event.data['message'] as String? ??
                event.data['detail'] as String? ??
                '本轮生成失败，请重试',
          );
        }
        if (event.type == 'done') {
          if (event.data['answer'] is! String ||
              event.data['sources'] is! List) {
            throw const ApiException('完成事件缺少回答或来源');
          }
          completed = true;
        }
        yield event;
        if (completed) break;
      }
      if (!completed) throw const ApiException('连接提前结束，本轮未保存，请重试');
    } on TimeoutException {
      throw const ApiException('云端连接超时；本机记忆仍可离线管理');
    } on http.ClientException {
      throw const ApiException('云端连接中断；请联网后重试，本机没有离线生成模型');
    } finally {
      if (identical(_active, client)) _active = null;
      client.close();
    }
  }

  Future<Json> summarize(Json body) async {
    validateRequest(body);
    return api.call('POST', '/agent/transient/summarize', body: body);
  }
}
