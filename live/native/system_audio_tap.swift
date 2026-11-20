// system-audio-tap: records what all apps play on macOS 14.4+ with a Core Audio process tap. No driver.
//
// system-audio-tap            Writes one JSON header line {"rate": R, "channels": C} to stdout, then raw
//                             float32 interleaved frames, until stdin closes.
// system-audio-tap --status   Prints the System Audio Recording permission: granted, denied, undetermined or unknown.
// system-audio-tap --request  Asks for the permission (shows the macOS prompt once), then prints the result.
//
// Exit codes: 0 done, 2 bad arguments, 3 Core Audio error, 4 permission denied.

import AudioToolbox
import CoreAudio
import Foundation

func fail(_ message: String, _ code: Int32 = 3) -> Never {
    FileHandle.standardError.write("system-audio-tap: \(message)\n".data(using: .utf8)!)
    exit(code)
}

func check(_ status: OSStatus, _ what: String) {
    if status != noErr { fail("\(what) failed (OSStatus \(status))") }
}

// MARK: Permission (TCC service kTCCServiceAudioCapture, private API, loaded at run time)

typealias PreflightFunc = @convention(c) (CFString, CFDictionary?) -> Int
typealias RequestFunc = @convention(c) (CFString, CFDictionary?, @escaping (Bool) -> Void) -> Void

let tcc = dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW)
let service = "kTCCServiceAudioCapture" as CFString

func permissionStatus() -> String {
    guard let tcc, let symbol = dlsym(tcc, "TCCAccessPreflight") else { return "unknown" }
    let preflight = unsafeBitCast(symbol, to: PreflightFunc.self)
    switch preflight(service, nil) {
    case 0: return "granted"
    case 1: return "denied"
    case 2: return "undetermined"
    default: return "unknown"
    }
}

func requestPermission() -> String {
    guard let tcc, let symbol = dlsym(tcc, "TCCAccessRequest") else { return "unknown" }
    let request = unsafeBitCast(symbol, to: RequestFunc.self)
    let done = DispatchSemaphore(value: 0)
    var granted = false
    request(service, nil) { result in
        granted = result
        done.signal()
    }
    done.wait()
    return granted ? "granted" : "denied"
}

// MARK: Tap

func defaultOutputDeviceUID() -> String {
    var device = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    check(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device),
          "reading the default output device")
    var uid: Unmanaged<CFString>?
    size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    address.mSelector = kAudioDevicePropertyDeviceUID
    check(AudioObjectGetPropertyData(device, &address, 0, nil, &size, &uid), "reading the output device UID")
    return uid!.takeRetainedValue() as String
}

func tapFormat(_ tap: AudioObjectID) -> AudioStreamBasicDescription {
    var format = AudioStreamBasicDescription()
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &format), "reading the tap format")
    return format
}

func record() {
    if permissionStatus() == "denied" {
        fail("System Audio Recording permission is denied. Allow it in System Settings > Privacy & Security", 4)
    }
    let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
    description.uuid = UUID()
    description.isPrivate = true
    description.muteBehavior = .unmuted
    var tap = AudioObjectID(kAudioObjectUnknown)
    check(AudioHardwareCreateProcessTap(description, &tap), "creating the process tap")

    let outputUID = defaultOutputDeviceUID()
    let aggregate: [String: Any] = [
        kAudioAggregateDeviceNameKey: "Transcription system audio",
        kAudioAggregateDeviceUIDKey: UUID().uuidString,
        kAudioAggregateDeviceMainSubDeviceKey: outputUID,
        kAudioAggregateDeviceIsPrivateKey: true,
        kAudioAggregateDeviceIsStackedKey: false,
        kAudioAggregateDeviceTapAutoStartKey: true,
        kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
        kAudioAggregateDeviceTapListKey: [
            [kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: description.uuid.uuidString]
        ],
    ]
    var device = AudioObjectID(kAudioObjectUnknown)
    check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &device), "creating the aggregate device")

    let format = tapFormat(tap)
    let channels = Int(format.mChannelsPerFrame)
    let isFloat = format.mFormatFlags & kAudioFormatFlagIsFloat != 0 && format.mBitsPerChannel == 32
    if !isFloat { fail("the tap format is not 32-bit float") }
    let header = "{\"rate\": \(Int(format.mSampleRate)), \"channels\": \(channels)}\n"
    FileHandle.standardOutput.write(header.data(using: .utf8)!)

    let queue = DispatchQueue(label: "system-audio-tap.io")
    var proc: AudioDeviceIOProcID?
    var interleaved = [Float]()
    check(AudioDeviceCreateIOProcIDWithBlock(&proc, device, queue) { _, input, _, _, _ in
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        if buffers.count == 1 {
            if let data = buffers[0].mData {
                FileHandle.standardOutput.write(Data(bytes: data, count: Int(buffers[0].mDataByteSize)))
            }
            return
        }
        let frames = Int(buffers[0].mDataByteSize) / MemoryLayout<Float>.size
        interleaved = [Float](repeating: 0, count: frames * buffers.count)
        for (channel, buffer) in buffers.enumerated() {
            guard let data = buffer.mData?.assumingMemoryBound(to: Float.self) else { continue }
            for frame in 0..<frames { interleaved[frame * buffers.count + channel] = data[frame] }
        }
        interleaved.withUnsafeBytes { FileHandle.standardOutput.write(Data($0)) }
    }, "creating the IO proc")
    check(AudioDeviceStart(device, proc), "starting the aggregate device")

    while (try? FileHandle.standardInput.read(upToCount: 4096)).map({ !($0?.isEmpty ?? true) }) ?? false {}

    AudioDeviceStop(device, proc)
    AudioDeviceDestroyIOProcID(device, proc!)
    AudioHardwareDestroyAggregateDevice(device)
    AudioHardwareDestroyProcessTap(tap)
}

signal(SIGPIPE, SIG_IGN)
switch CommandLine.arguments.dropFirst().first {
case nil: record()
case "--status": print(permissionStatus())
case "--request": print(requestPermission())
default: fail("unknown argument. Use no argument, --status or --request", 2)
}
