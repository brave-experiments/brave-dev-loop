#!/usr/bin/env python3
"""Transcribe an audio file to text using faster-whisper.

Usage: uv run --with faster-whisper scripts/transcribe-audio.py <audio_file>

Outputs the transcribed text to stdout. Exits with code 1 on failure.
"""
import sys


def transcribe(audio_path):
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, beam_size=5)
    return " ".join(segment.text.strip() for segment in segments)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: transcribe-audio.py <audio_file>", file=sys.stderr)
        sys.exit(1)
    try:
        text = transcribe(sys.argv[1])
        if text:
            print(text)
        else:
            print("(empty transcription)", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Transcription failed: {e}", file=sys.stderr)
        sys.exit(1)
