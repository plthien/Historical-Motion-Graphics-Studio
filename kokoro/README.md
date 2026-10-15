# Local Kokoro Voice Generation Runtime

Kokoro Local is an active production Voice generation provider. It generates narration audio through the local ONNX runtime and an explicit runtime-catalog voice ID. Timing still comes only from ElevenLabs Forced Alignment on the final paced WAV; Kokoro is never an alignment provider.

This directory owns the repository Kokoro ONNX command wrapper and setup scripts. Model binaries, the local runtime venv, generated WAV files, and the setup marker stay local and are ignored by Git.

Configure the application with `KOKORO_BASE_PATH` and `KOKORO_COMMAND`. The application also requires an ElevenLabs API key because forced alignment remains mandatory for final timing.

## Prerequisites

- Windows with Python 3.12 x64 available as `py -3.12` or `python`.
- Network access to PyPI and the upstream Kokoro ONNX release assets during setup.
- The application backend environment remains separate from the isolated Kokoro environment.

## Standalone setup

Run from the repository root:

```bat
kokoro\setup_kokoro.bat
```

The setup creates `kokoro\.venv`, installs `kokoro\requirements.txt`, downloads the local model/voice assets, validates imports, runs a standalone synthesis smoke through `kokoro_cli.py`, verifies mono 48 kHz PCM16 WAV output, and writes the local `.yas3_kokoro_ready.json` tooling marker.

The setup marker is part of Kokoro runtime readiness. The application also validates the configured command, model files, voice catalog, and selected voice before generation.

## Standalone voice catalog

```bat
kokoro\.venv\Scripts\python.exe kokoro\kokoro_cli.py --list-voices --format json
```

Catalog discovery reads the local voice bundle metadata without initializing the ONNX model. The wrapper exposes only voice/language combinations supported by its own verified tokenizer contract.

## Standalone synthesis smoke

```bat
kokoro\.venv\Scripts\python.exe kokoro\kokoro_cli.py --input-file path\to\input.txt --output-file path\to\output.wav --voice af_bella --language en-us --speaking-rate 1.0 --sample-rate 48000 --output-format wav_pcm_s16le
```

The output must be RIFF/WAVE, mono, 48,000 Hz, PCM signed 16-bit little-endian.

## Repository boundary

Allowed uses:

- production Kokoro Local narration generation through the Voice API/UI;
- isolated CLI development;
- setup-script validation;
- dependency/tooling CI;
- experiments whose outputs are not treated as production Voice artifacts.

Forbidden uses:

- Kokoro alignment or word/character timing authority;
- storing Kokoro voice IDs in Channel Voice Profile;
- pretending Kokoro is an ElevenLabs `voice_preset`;
- provider failover that silently switches generation providers;
- compatibility readers or switches that restore the retired Kokoro path.

If production Voice behavior is being changed, follow the active Voice architecture in `docs/99_SOURCE_OF_TRUTH.md`.
