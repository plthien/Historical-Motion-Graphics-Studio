#!/usr/bin/env python3
"""Repository-owned Kokoro ONNX command wrapper for YAS3 Voice."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import wave

try:
    from runtime_response_calibration import (
        KOKORO_FLOW_VERSION,
        RUNTIME_RESPONSE_RATES,
        RUNTIME_RESPONSE_TEXTS,
        RUNTIME_RESPONSE_TEXT_SHA256,
        RUNTIME_RESPONSE_TEXT_WORD_COUNTS,
        derive_native_wpm_at_rate_1,
        derive_runtime_response_beta,
        derive_runtime_native_wpm_at_rate_1,
        derive_runtime_response_sample,
    )
except ModuleNotFoundError:  # pragma: no cover - used by repo test/import contexts.
    from kokoro.runtime_response_calibration import (
        KOKORO_FLOW_VERSION,
        RUNTIME_RESPONSE_RATES,
        RUNTIME_RESPONSE_TEXTS,
        RUNTIME_RESPONSE_TEXT_SHA256,
        RUNTIME_RESPONSE_TEXT_WORD_COUNTS,
        derive_native_wpm_at_rate_1,
        derive_runtime_response_beta,
        derive_runtime_native_wpm_at_rate_1,
        derive_runtime_response_sample,
    )

CANONICAL_SAMPLE_RATE_HZ = 48_000
CANONICAL_CHANNELS = 1
CANONICAL_SAMPLE_WIDTH_BYTES = 2
CANONICAL_OUTPUT_FORMAT = "wav_pcm_s16le"
MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"
DEFAULT_MAX_CHUNK_CHARACTERS = 800

_LANGUAGE_MAP = {
    "en": "en-us",
    "en-us": "en-us",
    "en_us": "en-us",
    "us": "en-us",
    "american": "en-us",
    "en-gb": "en-gb",
    "en_uk": "en-gb",
    "en-uk": "en-gb",
    "gb": "en-gb",
    "british": "en-gb",
}

_SUPPORTED_VOICE_PREFIX_LANGUAGE_MAP = {
    "af": "en-us",
    "am": "en-us",
    "bf": "en-gb",
    "bm": "en-gb",
}
_SUPPORTED_LANGUAGES = frozenset(_SUPPORTED_VOICE_PREFIX_LANGUAGE_MAP.values())


class KokoroCliError(RuntimeError):
    """Controlled CLI failure surfaced through stderr and a non-zero exit."""


def _runtime_dir() -> Path:
    return Path(__file__).resolve().parent


def _model_path() -> Path:
    return (_runtime_dir() / MODEL_FILENAME).resolve()


def _voices_path() -> Path:
    return (_runtime_dir() / VOICES_FILENAME).resolve()


def normalize_language(language: str) -> str:
    value = (language or "").strip().lower().replace("_", "-")
    if not value:
        return "en-us"
    normalized = _LANGUAGE_MAP.get(value, value)
    if normalized not in _SUPPORTED_LANGUAGES:
        raise KokoroCliError(f"Unsupported Kokoro language for this wrapper: {language}")
    return normalized


def split_text_for_kokoro(text: str, *, max_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS) -> list[str]:
    if max_characters < 32:
        raise KokoroCliError("Kokoro max chunk characters must be at least 32.")
    clean = text.strip()
    if not clean:
        return []
    if len(clean) <= max_characters:
        return [clean]

    sentence_parts = re.findall(r".+?(?:[.!?]+(?:\s+|$)|\n+|$)", clean, flags=re.DOTALL)
    candidates = [part for part in sentence_parts if part.strip()] or [clean]
    chunks: list[str] = []
    current = ""

    def push(value: str) -> None:
        stripped = value.strip()
        if stripped:
            chunks.append(stripped)

    for candidate in candidates:
        if len(candidate) > max_characters:
            if current:
                push(current)
                current = ""
            for token in re.findall(r"\S+\s*", candidate):
                if len(token) > max_characters:
                    for index in range(0, len(token), max_characters):
                        push(token[index : index + max_characters])
                    continue
                if current and len(current) + len(token) > max_characters:
                    push(current)
                    current = token
                else:
                    current += token
            continue
        if current and len(current) + len(candidate) > max_characters:
            push(current)
            current = candidate
        else:
            current += candidate
    if current:
        push(current)
    return chunks


def _load_engine(model_path: Path, voices_path: Path):
    if not model_path.is_file():
        raise KokoroCliError(f"Kokoro model file is missing: {model_path}")
    if not voices_path.is_file():
        raise KokoroCliError(f"Kokoro voices file is missing: {voices_path}")
    try:
        from kokoro_onnx import Kokoro
    except Exception as exc:  # pragma: no cover - exercised by setup/manual runtime.
        raise KokoroCliError(f"Unable to import kokoro_onnx: {exc}") from exc
    try:
        return Kokoro(str(model_path), str(voices_path))
    except Exception as exc:  # pragma: no cover - depends on local model/runtime.
        raise KokoroCliError(f"Unable to initialize Kokoro runtime: {exc}") from exc


def _voice_ids_from_bundle(voices_path: Path) -> list[str]:
    if not voices_path.is_file():
        raise KokoroCliError(f"Kokoro voices file is missing: {voices_path}")
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - exercised by setup/manual runtime.
        raise KokoroCliError(f"Unable to import numpy: {exc}") from exc
    try:
        loaded = np.load(voices_path, allow_pickle=False)
    except Exception as exc:
        raise KokoroCliError(f"Unable to read Kokoro voices bundle: {exc}") from exc
    try:
        voice_ids = getattr(loaded, "files", None)
        if not isinstance(voice_ids, list):
            raise KokoroCliError("Kokoro voices bundle is not a supported NumPy archive.")
        return [str(item) for item in voice_ids]
    finally:
        close = getattr(loaded, "close", None)
        if callable(close):
            close()


def _display_name_from_voice_id(voice_id: str) -> str:
    suffix = voice_id.split("_", 1)[1] if "_" in voice_id else voice_id
    return " ".join(part.capitalize() for part in suffix.split("_") if part) or voice_id


def _language_from_voice_id(voice_id: str) -> str | None:
    prefix = voice_id.split("_", 1)[0].lower()
    return _SUPPORTED_VOICE_PREFIX_LANGUAGE_MAP.get(prefix)


def list_available_voices(voice_ids: list[str] | tuple[str, ...] | set[str] | None = None) -> dict[str, list[dict[str, str]]]:
    raw_voice_ids = voice_ids if voice_ids is not None else _voice_ids_from_bundle(_voices_path())
    voice_ids = sorted({item.strip() for item in raw_voice_ids if item.strip() and _language_from_voice_id(item.strip()) is not None})
    if not voice_ids:
        raise KokoroCliError("Kokoro voices bundle reported no supported voices.")
    return {
        "voices": [
            {
                "voice_id": voice_id,
                "display_name": _display_name_from_voice_id(voice_id),
                "language": _language_from_voice_id(voice_id) or "",
            }
            for voice_id in voice_ids
        ]
    }


def _flatten_samples(samples: object) -> list[float]:
    if hasattr(samples, "tolist"):
        data = samples.tolist()  # type: ignore[assignment]
    else:
        data = samples
    if data is None:
        return []
    if not isinstance(data, list):
        data = list(data)  # type: ignore[arg-type]
    if not data:
        return []
    first = data[0]
    if isinstance(first, (list, tuple)):
        rows = [list(row) for row in data if isinstance(row, (list, tuple)) and row]
        if not rows:
            return []
        if len(rows) <= 8 and len(rows[0]) > len(rows):
            frame_count = min(len(row) for row in rows)
            return [sum(float(row[index]) for row in rows) / len(rows) for index in range(frame_count)]
        return [sum(float(value) for value in frame) / len(frame) for frame in rows]
    return [float(value) for value in data]


def _resample_linear(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if source_rate <= 0:
        raise KokoroCliError(f"Kokoro returned an invalid sample rate: {source_rate}")
    if source_rate == target_rate:
        return samples
    if len(samples) <= 1:
        return samples[:]
    target_count = max(1, int(round(len(samples) * target_rate / source_rate)))
    if target_count == 1:
        return [samples[0]]
    scale = (len(samples) - 1) / (target_count - 1)
    output: list[float] = []
    for index in range(target_count):
        position = index * scale
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(samples[left] * (1.0 - fraction) + samples[right] * fraction)
    return output


def _float_samples_to_pcm16(samples: list[float]) -> bytes:
    output = bytearray()
    for value in samples:
        if not math.isfinite(value):
            value = 0.0
        clipped = max(-1.0, min(1.0, float(value)))
        if clipped <= -1.0:
            integer = -32768
        else:
            integer = int(round(clipped * 32767.0))
        output.extend(integer.to_bytes(2, "little", signed=True))
    return bytes(output)


def _write_canonical_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    if not pcm:
        raise KokoroCliError("Kokoro produced no PCM samples.")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(CANONICAL_CHANNELS)
        wav.setsampwidth(CANONICAL_SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


def validate_canonical_wav(path: Path, *, expected_sample_rate: int = CANONICAL_SAMPLE_RATE_HZ) -> None:
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
    except (wave.Error, EOFError) as exc:
        raise KokoroCliError(f"Output is not a valid WAV file: {exc}") from exc
    if compression != "NONE" or channels != 1 or width != 2 or rate != expected_sample_rate or frames <= 0:
        raise KokoroCliError(
            "Output WAV is not canonical mono PCM16 at "
            f"{expected_sample_rate} Hz: channels={channels}, width={width}, rate={rate}, frames={frames}, compression={compression}"
        )


def synthesize_to_wav(
    *,
    text: str,
    output_path: Path,
    voice: str,
    language: str,
    speaking_rate: float,
    sample_rate: int,
    output_format: str,
    engine: object | None = None,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> None:
    if output_format != CANONICAL_OUTPUT_FORMAT:
        raise KokoroCliError(f"Unsupported output format: {output_format}")
    if sample_rate != CANONICAL_SAMPLE_RATE_HZ:
        raise KokoroCliError(f"Unsupported sample rate: {sample_rate}")
    if speaking_rate <= 0:
        raise KokoroCliError("Speaking rate must be positive.")
    chunks = split_text_for_kokoro(text, max_characters=max_chunk_characters)
    if not chunks:
        raise KokoroCliError("Narration text is empty.")

    kokoro_language = normalize_language(language)
    expected_language = _language_from_voice_id(voice)
    if expected_language is None:
        raise KokoroCliError(f"Unsupported Kokoro voice for this wrapper: {voice}")
    if kokoro_language != expected_language:
        raise KokoroCliError(f"Kokoro voice {voice} requires language {expected_language}, got {kokoro_language}.")
    active_engine = engine or _load_engine(_model_path(), _voices_path())
    rendered: list[float] = []
    for chunk in chunks:
        try:
            samples, native_rate = active_engine.create(
                chunk,
                voice=voice,
                speed=speaking_rate,
                lang=kokoro_language,
                sentence_pause=0.0,
                clause_pause=0.0,
            )
        except Exception as exc:  # pragma: no cover - depends on local model/runtime.
            raise KokoroCliError(f"Kokoro synthesis failed: {exc}") from exc
        mono = _flatten_samples(samples)
        if not mono:
            raise KokoroCliError("Kokoro returned an empty audio chunk.")
        rendered.extend(_resample_linear(mono, int(native_rate), sample_rate))

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent, delete=False)
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        _write_canonical_wav(temp_path, _float_samples_to_pcm16(rendered), sample_rate)
        validate_canonical_wav(temp_path, expected_sample_rate=sample_rate)
        os.replace(temp_path, output_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def measure_runtime_response(*, engine: object | None = None, voice_ids: list[str] | None = None) -> dict[str, object]:
    catalog = list_available_voices(voice_ids)
    voices = catalog["voices"]
    selected = next((item for item in voices if item["voice_id"] == "af_heart"), voices[0])
    active_engine = engine or _load_engine(_model_path(), _voices_path())
    samples: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="yas3_kokoro_response_") as temp_dir:
        root = Path(temp_dir)
        for text_id, text in RUNTIME_RESPONSE_TEXTS:
            durations: dict[str, float] = {}
            for rate in RUNTIME_RESPONSE_RATES:
                output = root / f"{text_id}-{rate}.wav"
                synthesize_to_wav(
                    text=text,
                    output_path=output,
                    voice=selected["voice_id"],
                    language=selected["language"],
                    speaking_rate=rate,
                    sample_rate=CANONICAL_SAMPLE_RATE_HZ,
                    output_format=CANONICAL_OUTPUT_FORMAT,
                    engine=active_engine,
                )
                with wave.open(str(output), "rb") as wav:
                    durations[str(rate)] = round(wav.getnframes() / wav.getframerate(), 6)
            derived = derive_runtime_response_sample(durations)
            if derived is None:
                raise KokoroCliError(f"Runtime response calibration produced no valid beta for {text_id}.")
            pair_betas, text_beta = derived
            canonical_word_count = RUNTIME_RESPONSE_TEXT_WORD_COUNTS[text_id]
            native_wpm_at_rate_1 = derive_native_wpm_at_rate_1(
                canonical_word_count,
                durations[str(1.0)],
            )
            if native_wpm_at_rate_1 is None:
                raise KokoroCliError(f"Runtime response calibration produced no valid native WPM for {text_id}.")
            samples.append({
                "sample_text_id": text_id,
                "text_sha256": RUNTIME_RESPONSE_TEXT_SHA256[text_id],
                "canonical_word_count": canonical_word_count,
                "durations_seconds": durations,
                "pair_beta_estimates": pair_betas,
                "text_beta": text_beta,
                "native_wpm_at_rate_1": native_wpm_at_rate_1,
            })
    runtime_beta = derive_runtime_response_beta([float(item["text_beta"]) for item in samples])
    if runtime_beta is None:
        raise KokoroCliError("Runtime response calibration produced no valid runtime beta.")
    runtime_native_wpm = derive_runtime_native_wpm_at_rate_1(
        [float(item["native_wpm_at_rate_1"]) for item in samples]
    )
    if runtime_native_wpm is None:
        raise KokoroCliError("Runtime response calibration produced no valid runtime native WPM.")
    return {
        "flow_version": KOKORO_FLOW_VERSION,
        "voice_id": selected["voice_id"],
        "rates": list(RUNTIME_RESPONSE_RATES),
        "samples": samples,
        "speed_response_exponent": runtime_beta,
        "native_wpm_at_rate_1": runtime_native_wpm,
    }

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize canonical YAS3 Voice WAV through local Kokoro ONNX.")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument("--measure-runtime-response", action="store_true")
    parser.add_argument("--format", choices=("json",), default="json")
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--output-file", type=Path)
    parser.add_argument("--voice")
    parser.add_argument("--language")
    parser.add_argument("--speaking-rate", type=float)
    parser.add_argument("--sample-rate", type=int)
    parser.add_argument("--output-format")
    parser.add_argument("--max-chunk-characters", type=int, default=DEFAULT_MAX_CHUNK_CHARACTERS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.list_voices:
            print(json.dumps(list_available_voices(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.measure_runtime_response:
            print(json.dumps(measure_runtime_response(), ensure_ascii=False, sort_keys=True))
            return 0
        required = {
            "--input-file": args.input_file,
            "--output-file": args.output_file,
            "--voice": args.voice,
            "--language": args.language,
            "--speaking-rate": args.speaking_rate,
            "--sample-rate": args.sample_rate,
            "--output-format": args.output_format,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise KokoroCliError(f"Missing required synthesis arguments: {', '.join(missing)}")
        try:
            text = args.input_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise KokoroCliError(f"Unable to read input file: {exc}") from exc
        synthesize_to_wav(
            text=text,
            output_path=args.output_file,
            voice=args.voice,
            language=args.language,
            speaking_rate=args.speaking_rate,
            sample_rate=args.sample_rate,
            output_format=args.output_format,
            max_chunk_characters=args.max_chunk_characters,
        )
        return 0
    except KokoroCliError as exc:
        print(f"[kokoro_cli] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - final CLI guard.
        print(f"[kokoro_cli] Unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
