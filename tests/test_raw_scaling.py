"""Every RAW frame must be decoded with the same scaling and white balance.

Found on a real R6 sequence: LibRaw's default auto-maximum rescaled any frame
whose brightest pixel was just above 75% of white, inventing +0.3-0.4 stops of
"flicker" on frames whose light was steady; and per-frame Auto WB leaked into
brightness. Deflicker then corrected flicker that wasn't there.
"""
import sys
import types

import numpy as np

from timelapse_core import measure


class FakeRaw:
    def __init__(self, calls, daylight):
        self.calls, self.daylight_whitebalance = calls, daylight
        self.raw_image_visible = np.full((8, 8), 2000, dtype=np.uint16)
        self.raw_colors_visible = np.tile(np.array([[0, 1], [3, 2]]), (4, 4))
        self.black_level_per_channel = [512, 512, 512, 512]
        self.white_level = 16383

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def postprocess(self, **kw):
        self.calls.append(kw)
        return np.full((40, 60, 3), 20000, dtype=np.uint16)


def fake_rawpy(monkeypatch, daylight):
    calls = []
    mod = types.SimpleNamespace(imread=lambda p: FakeRaw(calls, daylight),
                                ColorSpace=types.SimpleNamespace(sRGB="sRGB"),
                                LibRawNoThumbnailError=Exception, LibRawUnsupportedThumbnailError=Exception)
    monkeypatch.setitem(sys.modules, "rawpy", mod)
    return calls


def test_every_frame_scaled_the_same(monkeypatch, tmp_path):
    calls = fake_rawpy(monkeypatch, [2.1, 1.0, 1.5, 0.0])
    measure._load_raw(tmp_path / "a.CR3", "careful")
    kw = calls[0]
    assert kw["adjust_maximum_thr"] == 0.0          # no per-frame auto maximum
    assert kw["use_camera_wb"] is False and kw["use_auto_wb"] is False
    assert kw["user_wb"] == [2.1, 1.0, 1.5, 1.0]     # one fixed WB; the second green is never zero
    assert kw["no_auto_bright"] is True


def test_missing_daylight_balance_falls_back_to_unity(monkeypatch, tmp_path):
    calls = fake_rawpy(monkeypatch, [0.0, 0.0, 0.0, 0.0])
    measure._load_raw(tmp_path / "a.CR3", "careful")
    assert calls[0]["user_wb"] == [1.0, 1.0, 1.0, 1.0]
