# iOS build VM — operator runbook

The Windows-hosted macOS build environment for the XauOrderPad iOS app, and the `build.bat` that
drives it. This is the iOS sibling of [android/README.md](../../android/README.md) + `deploy.bat`.

**Why a full macOS VM and not a Linux SDK image?** The iOS toolchain (Xcode, `xcodebuild`, `codesign`)
is macOS-only and cannot cross-compile signed iOS apps on Linux/Windows — there is no `xcode` container
the way there is an `android-sdk` one. So the macOS step runs in a QEMU/KVM macOS VM
([`dockurr/macos`](https://hub.docker.com/r/dockurr/macos)).

> **Apple EULA:** macOS on non-Apple hardware is a gray area. Personal use only, at your own risk.

## How the handoff works (mirrors the Android build)

| Android | iOS |
|---|---|
| Build in a Linux container (`docker compose exec … gradlew`) | Build in the macOS VM (`bash /shared/build.sh`) |
| `app/build/` named volume → `cp` to `/out` | VM writes `.ipa` to `/shared/out` (a Windows bind-mount — no copy) |
| `deploy.bat` copies APK → `E:\temp\mt5_data\app-debug.apk` | `build.bat fetch` copies IPA → `E:\temp\mt5_data\XauOrderPad.ipa` |
| Install from Windows `adb` (USB) | Install from Windows **Sideloadly** (USB) |

The bridge is the **9p shared folder**: `./shared` (Windows) ↔ `/shared` (VM). Source goes in at
`/shared/src`; the built IPA comes out at `/shared/out`.

---

## 0. Prerequisites — the fragile part. Prove it with Hello World first.

The make-or-break requirement is **KVM inside WSL2 on Windows 11**:

1. Enable virtualization in BIOS/UEFI (VT-x / AMD-V) and Windows features **Hyper-V** + **Virtual
   Machine Platform** + **WSL**.
2. Docker Desktop → Settings → **WSL2 backend**.
3. `wsl --update`, then confirm `/dev/kvm` exists inside WSL (`ls -l /dev/kvm`). Missing ⇒ the VM won't
   boot (or crawls).

**Validate before porting real code:** bring the VM up (§1), install Xcode (§2), and archive a throwaway
SwiftUI "Hello World". If that fails on your hardware, fall back to GitHub Actions (see the plan's Risks
section) — the Swift codebase is identical, nothing is wasted.

## 1. Bring up the VM

```bat
cd d:\llm\ios\mt5plus\ios\build-vm
build.bat up            :: docker compose up -d  (never `run --rm` — it wipes the warm VM)
build.bat open          :: opens http://localhost:8006
```

Complete the macOS installer in the web viewer. Disk + everything you install persist in `.\storage`.

## 2. One-time inside macOS

1. Sign in with your **Apple ID** (a free one is enough).
2. Install **Xcode** (App Store or an Xcode `.xip`), then `xcode-select --install` and
   `sudo xcodebuild -license accept`.
3. Install XcodeGen: `brew install xcodegen` (install Homebrew first if needed).
4. Mount the shared bridge (needed each boot; add to Login Items to make it stick):
   `sudo mount_9p shared` — the Windows `.\shared` folder then appears at `/shared`.
5. *(Optional, for one-command builds)* enable **Remote Login**: System Settings → General → Sharing →
   Remote Login **on**. Note the VM's IP (`ipconfig getifaddr en0`) and set on Windows, e.g.
   `set XAU_VM_SSH=-p 22 you@<vm-ip>`. For unattended SSH, add your key and passwordless `sudo` for
   `mount_9p`.

## 3. Build → IPA on Windows

From `ios\build-vm` on Windows:

```bat
build.bat            :: up + sync source + build-in-VM + fetch the IPA
```

- **With `XAU_VM_SSH` set**, that's the whole story: it SSHes in, runs `/shared/build.sh`, and copies the
  unsigned `.ipa` to `E:\temp\mt5_data\XauOrderPad.ipa`.
- **Without SSH**, it syncs the source and then stops with instructions: run `bash /shared/build.sh` in
  the VM Terminal, then `build.bat fetch` on Windows to pull the IPA over.

Other commands: `build.bat sync` (push source only), `build.bat fetch` (retrieve the IPA), `build.bat
status`, `build.bat open`, `build.bat down`.

## 4. Install on the iPhone from Windows (USB)

Docker Desktop on Windows **cannot pass the USB iPhone into the container** — same limit as the Android
build — so install from the Windows side:

1. Install [**Sideloadly**](https://sideloadly.io/) + Apple's iTunes/iCloud device drivers (standalone
   installers, not the Microsoft Store versions).
2. Plug in the iPhone, tap **Trust**, enable **Developer Mode** (Settings → Privacy & Security).
3. Drag `E:\temp\mt5_data\XauOrderPad.ipa` into Sideloadly, enter your **free Apple ID**, install.

**Free Apple ID caveat:** the install expires after **7 days** — re-run Sideloadly (or let AltStore
auto-refresh over Wi-Fi). A paid Developer account ($99/yr) makes it 1 year.

---

## Files here

- `docker-compose.yml` / `Dockerfile` — the macOS VM service (pins `dockurr/macos`, exposes 8006/5900,
  bind-mounts `./storage` and `./shared`).
- `build.bat` — Windows orchestration (the `deploy.bat` counterpart).
- `shared/build.sh` — guest-side build (the `gradlew assembleDebug` counterpart): xcodegen + xcodebuild
  archive + unsigned export to `/shared/out`.
- `ExportOptions-unsigned.plist` — unsigned export options (signing happens later in Sideloadly).
- `shared/` — the 9p bridge; `shared/src` (synced source) and `shared/out` (built IPA) appear here.

## Troubleshooting

- **`bash: /shared/build.sh: bad interpreter`** — the script has Windows CRLF line endings. Fix in the
  VM once: `sed -i '' -e 's/\r$//' /shared/build.sh` (or keep it LF in your editor).
- **`/shared` is empty in the VM** — run `sudo mount_9p shared`, and on Windows run `build.bat sync`.
- **VM won't boot / is extremely slow** — `/dev/kvm` isn't reaching the container; revisit §0.
