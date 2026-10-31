import numpy as np

from app.capture.vad import SAMPLE_RATE, UtteranceSegmenter

FRAME = 480


def tone(seconds, amp=0.2, freq=220):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds, noise=0.0005):
    rng = np.random.default_rng(0)
    return (noise * rng.standard_normal(int(seconds * SAMPLE_RATE))).astype(np.float32)


def run(signal, seg=None):
    seg = seg or UtteranceSegmenter()
    out = []
    for i in range(0, len(signal), FRAME):
        out += seg.feed(signal[i : i + FRAME])
    out += seg.flush()
    return out


def test_one_utterance_with_timestamps():
    sig = np.concatenate([silence(1.0), tone(1.5), silence(1.2)])
    (u,) = run(sig)
    assert 0.6 <= u.start_s <= 1.05 and 2.4 <= u.end_s <= 3.4
    assert u.samples.dtype == np.float32 and len(u.samples) > SAMPLE_RATE


def test_two_utterances_split_on_pause():
    sig = np.concatenate([silence(0.5), tone(1.0), silence(1.0), tone(1.0), silence(1.0)])
    assert len(run(sig)) == 2


def test_short_pause_does_not_split_a_sentence():
    sig = np.concatenate([silence(0.3), tone(1.0), silence(0.3), tone(1.0), silence(1.0)])
    assert len(run(sig)) == 1


def test_noise_and_clicks_are_ignored():
    sig = np.concatenate([silence(2.0), tone(0.1), silence(2.0)])  # 100 ms blip < min speech
    assert run(sig) == []
    assert run(silence(5.0)) == []


def test_long_speech_is_force_split_and_nothing_is_lost():
    sig = np.concatenate([tone(45.0, amp=0.2), silence(1.0)])
    out = run(sig, UtteranceSegmenter(max_utterance_s=20.0))
    assert len(out) >= 3
    total = sum(len(u.samples) for u in out) / SAMPLE_RATE
    assert total >= 44.0


def test_flush_emits_trailing_speech():
    sig = np.concatenate([silence(0.5), tone(1.2)])  # stream ends mid-speech
    assert len(run(sig)) == 1


def test_no_audio_is_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run(np.concatenate([silence(0.5), tone(1.0), silence(1.0)]))
    assert list(tmp_path.iterdir()) == []
