// Derive only the public verification value; never print private key material.
import Foundation
import CryptoKit

guard CommandLine.arguments.count == 2,
      let encoded = try? String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8),
      let seed = Data(base64Encoded: encoded.trimmingCharacters(in: .whitespacesAndNewlines)),
      let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: seed) else {
  fputs("Cannot read the original 32-byte update signing seed.\n", stderr)
  exit(1)
}
print(key.publicKey.rawRepresentation.base64EncodedString())
