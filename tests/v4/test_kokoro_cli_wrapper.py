from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    RUNTIME_RESPONSE_TEXTS,
    RUNTIME_RESPONSE_TEXT_WORD_COUNTS,
)

from app.modules.v4.script_word_budget import count_narration_words

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("yas3_kokoro_cli", ROOT / "kokoro" / "kokoro_cli.py")
assert SPEC is not None and SPEC.loader is not None
kokoro_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kokoro_cli)


class _FakeEngine:
    def __init__(self, *, sample_rate: int = 24_000, fail: bool = False) -> None:
        self.sample_rate = sample_rate
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def create(self, text: str, *, voice: str, speed: float, lang: str, sentence_pause: float | None = None, clause_pause: float | None = None):
        self.calls.append({
            "text": text,
            "voice": voice,
            "speed": speed,
            "lang": lang,
            "sentence_pause": sentence_pause,
            "clause_pause": clause_pause,
        })
        if self.fail:
            raise RuntimeError("synthetic failure")
        return [0.0, 0.25, -0.25, 0.0], self.sample_rate


class _ResponseEngine:
    def __init__(self, beta: float) -> None:
        self.beta = beta
        self.calls: list[tuple[str, float]] = []
        self.native_wpms = {
            text: native_wpm
            for (_text_id, text), native_wpm in zip(
                RUNTIME_RESPONSE_TEXTS,
                (180.0, 195.0, 210.0),
                strict=True,
            )
        }

    def create(self, text: str, *, voice: str, speed: float, lang: str, sentence_pause: float | None = None, clause_pause: float | None = None):
        _ = (voice, lang, sentence_pause, clause_pause)
        self.calls.append((text, speed))
        word_count = count_narration_words(text)
        duration_at_rate_1 = word_count * 60.0 / self.native_wpms[text]
        sample_count = max(1, round(duration_at_rate_1 * 48_000 / (speed ** self.beta)))
        return [0.0] * sample_count, 48_000


def test_wrapper_lists_normalized_voice_catalog_from_bundle_without_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voices_path = tmp_path / "voices-v1.0.bin"
    voices_path.write_bytes(b"fake-numpy-archive")
    archive = SimpleNamespace(files=["af_heart", "af_bella", "zf_xiaobei"], close=lambda: None)

    def fake_load(path: Path, *, allow_pickle: bool) -> object:
        assert path == voices_path
        assert allow_pickle is False
        return archive

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(load=fake_load))
    monkeypatch.setattr(kokoro_cli, "_voices_path", lambda: voices_path)
    monkeypatch.setattr(kokoro_cli, "_load_engine", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ONNX engine must not initialize for catalog listing")))

    result = kokoro_cli.list_available_voices()

    assert result == {
        "voices": [
            {"voice_id": "af_bella", "display_name": "Bella", "language": "en-us"},
            {"voice_id": "af_heart", "display_name": "Heart", "language": "en-us"},
        ]
    }


def test_wrapper_ignores_model_and_voices_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOKORO_MODEL_PATH", str(tmp_path / "foreign.onnx"))
    monkeypatch.setenv("KOKORO_VOICES_PATH", str(tmp_path / "foreign.bin"))

    assert kokoro_cli._model_path() == (ROOT / "kokoro" / "kokoro-v1.0.onnx").resolve()
    assert kokoro_cli._voices_path() == (ROOT / "kokoro" / "voices-v1.0.bin").resolve()


def test_wrapper_ignores_chunk_size_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOKORO_MAX_CHUNK_CHARACTERS", "64")

    assert kokoro_cli.parse_args([]).max_chunk_characters == 800
    assert kokoro_cli.parse_args(["--max-chunk-characters", "64"]).max_chunk_characters == 64


def test_wrapper_does_not_advertise_unsupported_language_families() -> None:
    result = kokoro_cli.list_available_voices(
        voice_ids=["af_heart", "am_adam", "bf_alice", "bm_daniel", "ff_siwis", "zf_xiaobei", "xx_voice"]
    )

    assert result == {
        "voices": [
            {"voice_id": "af_heart", "display_name": "Heart", "language": "en-us"},
            {"voice_id": "am_adam", "display_name": "Adam", "language": "en-us"},
            {"voice_id": "bf_alice", "display_name": "Alice", "language": "en-gb"},
            {"voice_id": "bm_daniel", "display_name": "Daniel", "language": "en-gb"},
        ]
    }


@pytest.mark.parametrize(
    ("voice_id", "language"),
    [
        ("af_heart", "en-us"),
        ("am_adam", "en-us"),
        ("bf_alice", "en-gb"),
        ("bm_daniel", "en-gb"),
    ],
)
def test_supported_language_matrix_reaches_wrapper_create(tmp_path: Path, voice_id: str, language: str) -> None:
    output = tmp_path / f"{voice_id}.wav"
    engine = _FakeEngine(sample_rate=48_000)

    kokoro_cli.synthesize_to_wav(
        text="Hello Kokoro.",
        output_path=output,
        voice=voice_id,
        language=language,
        speaking_rate=1.0,
        sample_rate=48_000,
        output_format="wav_pcm_s16le",
        engine=engine,
    )

    assert engine.calls == [{
        "text": "Hello Kokoro.",
        "voice": voice_id,
        "speed": 1.0,
        "lang": language,
        "sentence_pause": 0.0,
        "clause_pause": 0.0,
    }]


def test_wrapper_rejects_unsupported_voice_for_synthesis(tmp_path: Path) -> None:
    with pytest.raises(kokoro_cli.KokoroCliError, match="Unsupported Kokoro voice"):
        kokoro_cli.synthesize_to_wav(
            text="Hello.",
            output_path=tmp_path / "voice.wav",
            voice="zf_xiaobei",
            language="en-us",
            speaking_rate=1.0,
            sample_rate=48_000,
            output_format="wav_pcm_s16le",
            engine=_FakeEngine(),
        )


def test_wrapper_maps_language_resamples_and_writes_canonical_wav(tmp_path: Path) -> None:
    output = tmp_path / "voice.wav"
    engine = _FakeEngine(sample_rate=24_000)

    kokoro_cli.synthesize_to_wav(
        text="Hello Kokoro.",
        output_path=output,
        voice="af_heart",
        language="en",
        speaking_rate=1.0,
        sample_rate=48_000,
        output_format="wav_pcm_s16le",
        engine=engine,
    )

    assert engine.calls == [{
        "text": "Hello Kokoro.",
        "voice": "af_heart",
        "speed": 1.0,
        "lang": "en-us",
        "sentence_pause": 0.0,
        "clause_pause": 0.0,
    }]
    with wave.open(str(output), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 48_000
        assert wav.getnframes() > 0
        assert wav.getcomptype() == "NONE"


def test_wrapper_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(kokoro_cli.KokoroCliError, match="empty"):
        kokoro_cli.synthesize_to_wav(
            text="  \n\t",
            output_path=tmp_path / "voice.wav",
            voice="af_heart",
            language="en",
            speaking_rate=1.0,
            sample_rate=48_000,
            output_format="wav_pcm_s16le",
            engine=_FakeEngine(),
        )


def test_wrapper_preserves_existing_output_when_synthesis_fails(tmp_path: Path) -> None:
    output = tmp_path / "voice.wav"
    original = b"previous output"
    output.write_bytes(original)

    with pytest.raises(kokoro_cli.KokoroCliError, match="Kokoro synthesis failed"):
        kokoro_cli.synthesize_to_wav(
            text="This should fail.",
            output_path=output,
            voice="af_heart",
            language="en",
            speaking_rate=1.0,
            sample_rate=48_000,
            output_format="wav_pcm_s16le",
            engine=_FakeEngine(fail=True),
        )

    assert output.read_bytes() == original


def test_wrapper_internal_chunks_preserve_text_order(tmp_path: Path) -> None:
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    engine = _FakeEngine(sample_rate=48_000)

    kokoro_cli.synthesize_to_wav(
        text=text,
        output_path=tmp_path / "voice.wav",
        voice="af_heart",
        language="en",
        speaking_rate=1.0,
        sample_rate=48_000,
        output_format="wav_pcm_s16le",
        engine=engine,
        max_chunk_characters=32,
    )

    called_text = " ".join(str(item["text"]) for item in engine.calls)
    assert " ".join(called_text.split()) == " ".join(text.split())
    assert len(engine.calls) > 1


def test_wrapper_rejects_noncanonical_request(tmp_path: Path) -> None:
    with pytest.raises(kokoro_cli.KokoroCliError, match="Unsupported sample rate"):
        kokoro_cli.synthesize_to_wav(
            text="Hello.",
            output_path=tmp_path / "voice.wav",
            voice="af_heart",
            language="en",
            speaking_rate=1.0,
            sample_rate=24_000,
            output_format="wav_pcm_s16le",
            engine=_FakeEngine(),
        )


def test_wrapper_measures_runtime_response_with_one_vote_per_text() -> None:
    engine = _ResponseEngine(beta=0.65)

    result = kokoro_cli.measure_runtime_response(
        engine=engine,
        voice_ids=["bf_alice", "af_heart"],
    )

    assert result["flow_version"] == KOKORO_FLOW_VERSION
    assert result["voice_id"] == "af_heart"
    assert result["rates"] == [0.75, 1.0, 1.25, 1.5]
    assert len(result["samples"]) == 3
    assert len(engine.calls) == 12
    assert len({text for text, _rate in engine.calls}) == 3
    assert all(len(sample["pair_beta_estimates"]) == 6 for sample in result["samples"])
    assert all(sample["text_beta"] == pytest.approx(0.65, abs=0.01) for sample in result["samples"])
    assert result["speed_response_exponent"] == pytest.approx(0.65, abs=0.01)
    assert [sample["native_wpm_at_rate_1"] for sample in result["samples"]] == pytest.approx(
        [180.0, 195.0, 210.0],
        abs=0.01,
    )
    assert result["native_wpm_at_rate_1"] == pytest.approx(195.0, abs=0.01)
    assert {
        text_id: count_narration_words(text)
        for text_id, text in RUNTIME_RESPONSE_TEXTS
    } == RUNTIME_RESPONSE_TEXT_WORD_COUNTS
