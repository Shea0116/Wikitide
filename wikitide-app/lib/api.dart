import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'models.dart';

const defaultServer = 'https://wikitide.soulshea.vip';
const apiPrefix = '/api/client/v1';

class ApiException implements Exception {
  const ApiException(this.message, {this.status = 0, this.data});
  final String message;
  final int status;
  final Json? data;
  @override
  String toString() => message;
}

String normalizeServer(String value, {bool development = !kReleaseMode}) {
  final uri = Uri.tryParse(value.trim());
  if (uri == null ||
      !uri.hasAuthority ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      (uri.path.isNotEmpty && uri.path != '/')) {
    throw const ApiException('请输入完整的站点地址，例如 https://wikitide.soulshea.vip');
  }
  final local = {
    'localhost',
    '127.0.0.1',
    '::1',
    '10.0.2.2',
  }.contains(uri.host);
  if (uri.scheme != 'https' &&
      !(development && local && uri.scheme == 'http')) {
    throw const ApiException('站点需要使用 HTTPS');
  }
  return uri.replace(path: '').toString();
}

class WikiApi {
  WikiApi({String server = defaultServer, http.Client? client})
    : server = normalizeServer(server),
      _client = client ?? http.Client();
  String server;
  String? token;
  void Function()? onExpired;
  final http.Client _client;

  Uri url(String path, [Map<String, String>? query]) =>
      Uri.parse(server).resolve(path).replace(queryParameters: query);

  Future<Json> call(
    String method,
    String path, {
    Json? body,
    Map<String, String>? query,
    bool authenticated = true,
  }) async {
    final request = http.Request(method, url('$apiPrefix$path', query))
      ..followRedirects = false
      ..headers['Accept'] = 'application/json';
    if (authenticated && token != null) {
      request.headers['Authorization'] = 'Bearer $token';
    }
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    try {
      final response = await http.Response.fromStream(
        await _client.send(request).timeout(const Duration(seconds: 25)),
      ).timeout(const Duration(seconds: 25));
      Json? json;
      try {
        final decoded = jsonDecode(utf8.decode(response.bodyBytes));
        if (decoded is Map) json = Map<String, dynamic>.from(decoded);
      } on FormatException {
        /* A reverse proxy may return HTML on errors. */
      }
      if (response.statusCode >= 200 &&
          response.statusCode < 300 &&
          json != null) {
        return json;
      }
      if (response.statusCode == 401 && authenticated && token != null) {
        onExpired?.call();
      }
      final message = json?['detail'];
      throw ApiException(
        message is String
            ? message
            : response.statusCode == 404
            ? '服务器尚未启用客户端接口，或内容已不可访问'
            : '请求失败（${response.statusCode}），请稍后重试',
        status: response.statusCode,
        data: json,
      );
    } on TimeoutException {
      throw const ApiException('连接超时，请检查网络后重试');
    } on http.ClientException {
      throw const ApiException('无法连接站点，请检查网络和服务器地址');
    }
  }

  Future<http.Response> asset(Uri uri) async {
    // Tokens are never forwarded to external links or redirect targets.
    if (uri.origin != Uri.parse(server).origin ||
        !RegExp(r'^/api/client/notes/\d+/asset$').hasMatch(uri.path)) {
      throw const ApiException('附件地址无效');
    }
    final request = http.Request('GET', uri)..followRedirects = false;
    if (token != null) request.headers['Authorization'] = 'Bearer $token';
    try {
      final streamed = await _client
          .send(request)
          .timeout(const Duration(seconds: 25));
      if (streamed.statusCode != 200) {
        await streamed.stream.drain<void>();
        if (streamed.statusCode == 401 && token != null) onExpired?.call();
        throw ApiException(
          '附件无法访问（${streamed.statusCode}）',
          status: streamed.statusCode,
        );
      }
      final bytes = <int>[];
      await for (final chunk in streamed.stream.timeout(
        const Duration(seconds: 25),
      )) {
        bytes.addAll(chunk);
        if (bytes.length > 4 * 1024 * 1024) {
          throw const ApiException('附件超过 4 MB，暂不支持在客户端打开');
        }
      }
      return http.Response.bytes(bytes, 200, headers: streamed.headers);
    } on TimeoutException {
      throw const ApiException('附件加载超时，请重试');
    } on http.ClientException {
      throw const ApiException('附件加载失败，请检查网络');
    }
  }

  Future<NotePage> browse(String category, int page) async => NotePage.fromJson(
    await call('GET', '/notes', query: {'category': category, 'page': '$page'}),
  );
  Future<NotePage> search(String q, String category, int page) async =>
      NotePage.fromJson(
        await call(
          'GET',
          '/search',
          query: {'q': q, 'category': category, 'page': '$page'},
        ),
      );
  Future<NoteDetail> note(int id) async =>
      NoteDetail.fromJson(await call('GET', '/notes/$id'));
  void close() => _client.close();
}
