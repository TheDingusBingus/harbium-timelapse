"""Lightroom / Camera Raw through .xmp sidecars. The sample XMP below is made
up, in the same shape as real Lightroom Classic files (settings as attributes
of the first rdf:Description, masks with their own LocalExposure2012 further
down, very long lines, a stray Windows line ending)."""

import hashlib

import numpy as np
import pytest

import harbium_timelapse.editors.lightroom.adapter as lr_adapter
from harbium_timelapse import cli
from harbium_timelapse.editors import detect
from harbium_timelapse.editors.lightroom import Lightroom
from harbium_timelapse.editors.lightroom.xmp import format_exposure, read_exposure, with_exposure

N = 20
RAW = np.linspace(0, -0.8, N) + np.random.default_rng(2).normal(0, 0.06, N)


def sample_xmp(name: str, exposure: str) -> bytes:
    long_mask = "A" * 5000
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"\n'
        '    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        '   tiff:Model="Example Camera"\n'
        '   crs:Version="16.5"\n'
        '   crs:ProcessVersion="15.4"\n'
        '   crs:WhiteBalance="Custom"\n'
        '   crs:Temperature="5250"\n'
        '   crs:Tint="+6"\n'
        f'   crs:Exposure2012="{exposure}"\n'
        '   crs:Contrast2012="+24"\n'
        '   crs:Description="a &gt; b, quoted"\n'
        f'   crs:RawFileName="{name}.CR2"\n'
        '   crs:HasSettings="True">\r\n'
        '   <crs:MaskGroupBasedCorrections>\n'
        '    <rdf:Seq><rdf:li><rdf:Description crs:What="Correction" crs:LocalExposure2012="0.35"\n'
        f'      crs:LocalExposure="0.1" crs:MaskData="{long_mask}"/></rdf:li></rdf:Seq>\n'
        '   </crs:MaskGroupBasedCorrections>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    ).encode("utf-8")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    for i in range(N):
        (tmp_path / f"IMG_{1000 + i}.CR2").write_bytes(b"")
        (tmp_path / f"IMG_{1000 + i}.xmp").write_bytes(sample_xmp(f"IMG_{1000 + i}", ["0.00", "+0.42", "-0.15"][i % 3]))
    (tmp_path / "IMG_1003_1.xmp").write_bytes(sample_xmp("IMG_1003", "+1.00"))     # a copy: no photo
    (tmp_path / "IMG_1004.CR2.xmp").write_bytes(b"<darktable sidecar/>")             # darktable's, not ours
    monkeypatch.setattr(lr_adapter, "measure_brightness", lambda *a, **k: RAW)
    monkeypatch.setattr(lr_adapter, "running_programs", lambda: ["explorer.exe"])
    return tmp_path


def digest(folder):
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.xmp"))}


def test_lightroom_number_style():
    assert [format_exposure(v) for v in (0, 0.004, 0.42, -0.15, 1.2)] == ["0.00", "0.00", "+0.42", "-0.15", "+1.20"]


def test_only_the_exposure_changes_and_undo_is_exact():
    data = sample_xmp("IMG_1", "+0.42")
    assert read_exposure(data) == 0.42
    new = with_exposure(data, 0.77)
    assert read_exposure(new) == 0.77
    assert new.replace(b'Exposure2012="+0.77"', b'Exposure2012="+0.42"') == data   # nothing else moved
    assert b'LocalExposure2012="0.35"' in new and b"\r\n" in new                    # masks and line ending kept
    assert with_exposure(new, 0.42) == data
    with pytest.raises(ValueError, match="range"):
        with_exposure(data, 6.0)


def test_detected_and_listed(folder):
    assert [e.name for e in detect(folder)] == ["lightroom"]


def test_prepare_then_undo_end_to_end(folder, capsys):
    before = digest(folder)
    assert cli.main(["prepare", str(folder), "--force"]) == 0      # --force: the stand-in photos have no settings
    out = capsys.readouterr().out
    assert "Read Metadata from Files" in out and "White balance left as it is" in out
    after = digest(folder)
    assert after["IMG_1003_1.xmp"] == before["IMG_1003_1.xmp"] and after["IMG_1004.CR2.xmp"] == before["IMG_1004.CR2.xmp"]
    assert sum(after[k] != before[k] for k in before) >= N - 3
    assert cli.main(["undo", str(folder)]) == 0
    assert digest(folder) == before                                  # byte for byte


def test_missing_settings_are_refused_with_the_fix(folder, capsys):
    (folder / "IMG_1007.xmp").unlink()
    before = digest(folder)
    assert cli.main(["prepare", str(folder)]) == 1
    captured = capsys.readouterr()
    assert "Save Metadata to Files" in captured.out + captured.err
    assert digest(folder) == before


def test_refuses_while_lightroom_is_open(folder, monkeypatch, capsys):
    monkeypatch.setattr(lr_adapter, "running_programs", lambda: ["Lightroom.exe"])
    before = digest(folder)
    assert cli.main(["prepare", str(folder), "--editor", "lightroom"]) == 1
    assert "is open" in capsys.readouterr().err and digest(folder) == before


def test_interrupted_run_then_rerun_matches_a_clean_run(folder, tmp_path_factory, monkeypatch):
    clean = tmp_path_factory.mktemp("clean")
    for p in folder.iterdir():
        if p.is_file():
            (clean / p.name).write_bytes(p.read_bytes())
    ed = Lightroom()
    ed.prepare(ed.load(clean), raw_brightness=RAW, force=True)
    real, count = lr_adapter.write_bytes_atomically, {"n": 0}
    def cut(path, data):
        count["n"] += 1
        if count["n"] > 7:
            raise KeyboardInterrupt
        real(path, data)
    monkeypatch.setattr(lr_adapter, "write_bytes_atomically", cut)
    with pytest.raises(KeyboardInterrupt):
        ed.prepare(ed.load(folder), raw_brightness=RAW, force=True)
    monkeypatch.setattr(lr_adapter, "write_bytes_atomically", real)
    ed.prepare(ed.load(folder), raw_brightness=RAW, force=True)
    assert digest(folder) == digest(clean)


def test_running_prepare_twice_replaces_rather_than_stacks(folder):
    ed = Lightroom()
    ed.prepare(ed.load(folder), raw_brightness=RAW, force=True)
    once = digest(folder)
    ed.prepare(ed.load(folder), raw_brightness=RAW, force=True)
    assert digest(folder) == once
