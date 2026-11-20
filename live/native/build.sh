#!/bin/sh
# Build the macOS system audio helper into live/native/bin/. Needs the Xcode command line tools.
set -e
cd "$(dirname "$0")"
mkdir -p bin
swiftc -O -target "$(uname -m)-apple-macos14.4" system_audio_tap.swift -o bin/system-audio-tap
echo "built $(pwd)/bin/system-audio-tap"
