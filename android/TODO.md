# Android — backlog

## Hardening the demo APK against credential extraction (NOT yet implemented)

The `release demo` APK bakes in the URL + token + client cert, so anyone with the file can extract them
and trade the demo account. Agreed future hardening (see README → "Planned: hardening the demo APK"):

- [ ] **Time-derived login code** — friend enters a short code computed from the current clock each
      connect (e.g. 23:07 → 2+3+7 = 12); app refuses to connect on a mismatch. Use an hourly bucket (or
      ±1 min tolerance) for clock skew. **Fold in a shared PIN only the friend knows** (`sum + PIN`) so the
      public algorithm alone isn't enough.
- [ ] **R8 obfuscation** (`isMinifyEnabled = true` + `shrinkResources`) — needs kotlinx.serialization keep
      rules first (the reason R8 is off today, `app/build.gradle.kts:108`).
- [ ] **Android Keystore** for the unlocked cert + token (non-exportable) instead of plain files/prefs.
- [ ] Keep the bundled cert + token (current `release demo`) — the code is an added gate, not a replacement.

**Reality check:** this stack is layered obfuscation for a *demo* credential — a speed-bump, not a lock.
Only a secret that is **not** in the APK (a passphrase/activation code, or server-provisioned per-device
cert) gives real leak resistance. Do not use the baked APK for a real-money account.
