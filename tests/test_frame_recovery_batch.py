"""Regression tests for batch (whole-library) imports.

Corruption in a single frame of a single file must stay confined to that
frame: the file still loads and the walk over a directory of mixed healthy
and corrupt files visits every file instead of aborting midway.
"""
import argparse
from pathlib import Path

import eyed3
from eyed3.id3 import ID3_V2_4
from eyed3.id3.frames import FrameHeader
from eyed3.plugins import LoaderPlugin
from eyed3.utils import walk

_MPEG_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _frame(fid, data):
    return FrameHeader(fid, ID3_V2_4).render(len(data)) + data


def _tag_body(title, extra=b""):
    return _frame(b"TIT2", b"\x03" + title.encode("latin1")) + extra


def _write_mp3(path, tag_body, audio=_MPEG_FRAME * 4):
    size = len(tag_body)
    path.write_bytes(b"ID3" + bytes([4, 0, 0]) + bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F,
        (size >> 7) & 0x7F, size & 0x7F]) + tag_body + audio)


class _RecordingLoader(LoaderPlugin):
    def __init__(self, parser):
        super().__init__(parser, cache_files=True)
        self.seen = []
        self.loaded = []

    def handleFile(self, f, *args, **kwargs):
        super().handleFile(f, *args, **kwargs)
        self.seen.append(f)
        if self.audio_file is not None:
            self.loaded.append(self.audio_file)

    def handleDirectory(self, d, files):
        # The base implementation clears the file cache; keep our own copy.
        super().handleDirectory(d, files)


def test_batch_import_does_not_abort_on_corrupt_frame(tmp_path):
    _write_mp3(tmp_path / "01_good.mp3", _tag_body("one"))
    # APIC missing its picture-type byte (used to raise TypeError mid-import).
    _write_mp3(tmp_path / "02_corrupt.mp3",
               _tag_body("two", _frame(b"APIC", b"\x00image/jpeg\x00")))
    _write_mp3(tmp_path / "03_good.mp3", _tag_body("three"))

    handler = _RecordingLoader(argparse.ArgumentParser())
    handler.start(argparse.Namespace(), None)
    walk(handler, str(tmp_path), recursive=True)

    # Every file in the directory was visited and loaded, healthy ones too.
    assert len(handler.seen) == 3
    assert len(handler.loaded) == 3
    names = {Path(af.path).name for af in handler.loaded}
    assert names == {"01_good.mp3", "02_corrupt.mp3", "03_good.mp3"}
    # All three tags yielded their good frames, including the corrupt file.
    assert sorted(af.tag.title for af in handler.loaded) == ["one", "three", "two"]


def test_corrupt_frame_ids_available_to_callers(tmp_path):
    """Callers can inspect which frames were dropped (the error ledger)."""
    path = tmp_path / "f.mp3"
    _write_mp3(path, _tag_body("t", _frame(b"APIC", b"\x00")))
    af = eyed3.load(path)
    assert af.tag.frame_set.corrupt_frame_ids == {b"APIC"}
    assert af.tag.frame_set.unknown_frame_ids == set()


def _write_unsupported_version(path):
    # An ID3v2 tag whose minor version (5) the library does not support.
    tag_body = _tag_body("bad")
    size = len(tag_body)
    path.write_bytes(b"ID3" + bytes([5, 0, 0]) + bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F,
        (size >> 7) & 0x7F, size & 0x7F]) + tag_body + _MPEG_FRAME * 4)


def test_batch_import_skips_unreadable_tag_and_continues(tmp_path):
    """A per-file tag error is reported and skipped; the walk goes on."""
    _write_mp3(tmp_path / "01_good.mp3", _tag_body("one"))
    _write_unsupported_version(tmp_path / "02_bad_tag.mp3")
    _write_mp3(tmp_path / "03_good.mp3", _tag_body("three"))

    handler = _RecordingLoader(argparse.ArgumentParser())
    handler.start(argparse.Namespace(), None)
    walk(handler, str(tmp_path), recursive=True)

    # All files visited; the two healthy ones still load.
    assert len(handler.seen) == 3
    assert sorted(af.tag.title for af in handler.loaded) == ["one", "three"]
