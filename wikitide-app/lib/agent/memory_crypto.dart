import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';
import 'package:cryptography/cryptography.dart';

/// AES-GCM authenticates the library, table, record ID and revision as well as
/// the payload. Copying a ciphertext to another account cannot make it valid.
class MemoryCrypto {
  static final _cipher = AesGcm.with256bits();

  static Uint8List randomBytes(int count) {
    final random = Random.secure();
    return Uint8List.fromList(List.generate(count, (_) => random.nextInt(256)));
  }

  static String newId() =>
      base64Url.encode(randomBytes(18)).replaceAll('=', '');

  static Future<String> digest(String value) async => (await Sha256().hash(
    utf8.encode(value),
  )).bytes.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();

  static List<int> aad(
    String library,
    String table,
    String id,
    int revision, {
    String? status,
    String? projectId,
  }) => utf8.encode(
    jsonEncode([1, library, table, id, revision, status, projectId]),
  );

  static Future<Map<String, Object>> seal(
    Map<String, dynamic> payload,
    List<int> key,
    List<int> additionalData,
  ) async {
    final box = await _cipher.encrypt(
      utf8.encode(jsonEncode(payload)),
      secretKey: SecretKey(key),
      nonce: randomBytes(12),
      aad: additionalData,
    );
    return {
      'nonce': Uint8List.fromList(box.nonce),
      'ciphertext': Uint8List.fromList(box.cipherText),
      'mac': Uint8List.fromList(box.mac.bytes),
    };
  }

  static Future<Map<String, dynamic>> open(
    Map<String, dynamic> sealed,
    List<int> key,
    List<int> additionalData,
  ) async {
    final plaintext = await _cipher.decrypt(
      SecretBox(
        sealed['ciphertext'] as List<int>,
        nonce: sealed['nonce'] as List<int>,
        mac: Mac(sealed['mac'] as List<int>),
      ),
      secretKey: SecretKey(key),
      aad: additionalData,
    );
    return Map<String, dynamic>.from(jsonDecode(utf8.decode(plaintext)) as Map);
  }
}
