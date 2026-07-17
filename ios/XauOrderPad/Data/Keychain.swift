import Foundation
import Security

/// Minimal generic-password Keychain wrapper for the two on-device secrets that must not sit in
/// UserDefaults: the API token and the client-p12 password.
///
/// Android stores these in a private SharedPreferences (see Secrets.kt's reasoning); on iOS the
/// idiomatic secure spot is the Keychain, so we use it here. Values are per-app and survive
/// reinstall unless explicitly cleared.
enum Keychain {

    /// Store (or overwrite) `value` under `account`. Empty string clears the item.
    static func set(_ value: String, account: String) {
        guard !value.isEmpty else { remove(account: account); return }
        let data = Data(value.utf8)
        // Upsert: delete any existing item, then add. Simpler and race-free enough for our use.
        remove(account: account)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    static func get(account: String) -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data,
              let s = String(data: data, encoding: .utf8) else { return "" }
        return s
    }

    static func remove(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }

    private static let service = "com.xauorderpad.secrets"
}
