# live/native/

## How it works

- `system_audio_tap.swift` builds to `bin/system-audio-tap` with `build.sh` (swiftc, macOS 14.4 target). `bin/` is not in git; the packaged app has its own copy.
- No argument: a private, unmuted global process tap (`CATapDescription(stereoGlobalTapButExcludeProcesses: [])`), a private aggregate device with the default output device as main device and the tap in its tap list, then an IO block on a dispatch queue. Writes `{"rate": R, "channels": C}\n`, then raw float32 interleaved frames (non-interleaved buffers are interleaved first). Stops when stdin closes, then destroys the IO proc, the aggregate device and the tap.
- `--status`: `granted`, `denied`, `undetermined` or `unknown` (TCC service `kTCCServiceAudioCapture`). `--request`: shows the prompt, prints the result.
- Exit codes: 2 bad argument, 3 Core Audio error, 4 permission denied.
- `live.recorder.tap_stream` runs it; `desktop.permissions` calls `--status` and `--request`.

## Decisions

- See `live/ARCHITECTURE.md`, "macOS capture: Core Audio process tap".
- Swift, not Objective-C: the Core Audio tap API is plain C plus one Objective-C class, and Swift calls both with no bridging header. One file, built by one `swiftc` call.
- The aggregate device is private, so it does not show in the user's device list, and it is destroyed at the stop.
- The permission functions are loaded with `dlopen`/`dlsym`, so the helper still runs if a macOS version removes them: the status is then `unknown`.
