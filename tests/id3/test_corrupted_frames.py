# -*- coding: utf-8 -*-
"""Regression tests: a single corrupted ID3v2 frame must never abort loading
the whole file.

Before this fix, four families of frame-level damage escaped ``FrameSet.parse``'s
``except FrameException`` guard as raw Python exceptions and propagated out of
``eyed3.load`` -- losing the entire file (and, in a bulk importer, the whole
run). Each test below builds a tag with one healthy frame plus one corrupt
frame and asserts that:

  * the file still loads,
  * the healthy frame is intact,
  * the corrupt frame was dropped (the library's established tolerant
    "warn + skip + continue" behaviour).
"""
import os

import pytest

import eyed3
from eyed3.id3 import ID3_V2_3, ID3_V2_4
from eyed3.id3.frames import FrameException, createFrame
from eyed3.id3.headers import FrameHeader, TagHeader
from .. import DATA_D


pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(DATA_D, "notag-vbr.mp3")),
                                reason="test requires data files")


AUDIO_PATH = os.path.join(DATA_D, "notag-vbr.mp3")


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F,
                  (n >> 7) & 0x7F, n & 0x7F])


def _frame_v24(fid, data, flags=b"\x00\x00"):
    return fid + _syncsafe(len(data)) + flags + data


def _frame_v23(fid, data, flags=b"\x00\x00"):
    return fid + len(data).to_bytes(4, "big") + flags + data


def _healthy_title(minor):
    data = b"\x03Healthy"  # utf-8 text
    return _frame_v23(b"TIT2", data) if minor == 3 else _frame_v24(b"TIT2", data)


def _write_mp3(tmp_path, name, corrupt_blob, minor=4, title_first=True):
    """Prepend a one-healthy-frame + one-corrupt-frame tag to tagless audio."""
    with open(AUDIO_PATH, "rb") as f:
        audio = f.read()
    good = _healthy_title(minor)
    blob = good + corrupt_blob if title_first else corrupt_blob + good
    tag = b"ID3" + bytes([minor, 0, 0]) + _syncsafe(len(blob)) + blob
    path = tmp_path / name
    path.write_bytes(tag + audio)
    return path


def _assert_loads_with_healthy_frame(path, skipped_fid):
    audio = eyed3.load(str(path))
    assert audio is not None
    assert audio.tag is not None
    # The corrupt frame was skipped...
    assert skipped_fid not in audio.tag.frame_set
    # ...but the healthy frame in the same tag survived.
    assert audio.tag.title == "Healthy"
    return audio


def _assert_loads(path):
    """The file loads and the healthy frame survives. The damaged frame may be
    either skipped or degraded (the library recovers frames whose only damage
    is an unrecognized encoding byte by falling back to latin1)."""
    audio = eyed3.load(str(path))
    assert audio is not None
    assert audio.tag is not None
    assert audio.tag.title == "Healthy"
    return audio


# ---------------------------------------------------------------------------
# The four required corruption families
# ---------------------------------------------------------------------------
def test_unicode_decode_error_frame_is_skipped(tmp_path):
    """TXXX declares UTF-16 but carries a lone UTF-16 surrogate."""
    bad = _frame_v24(b"TXXX", b"\x01\xff\xfe\x00\xd8\x00\xd8")
    path = _write_mp3(tmp_path, "decode.mp3", bad)
    _assert_loads_with_healthy_frame(path, b"TXXX")


def test_type_error_frame_is_skipped(tmp_path):
    """APIC truncated right after the MIME type: no picture-type byte, so the
    old ``ord(b"")`` raised TypeError."""
    bad = _frame_v24(b"APIC", b"\x00image/jpeg\x00")
    path = _write_mp3(tmp_path, "type.mp3", bad)
    _assert_loads_with_healthy_frame(path, b"APIC")


def test_zlib_error_frame_is_skipped(tmp_path):
    """A v2.3 frame flagged compressed but holding a non-zlib payload."""
    bad = _frame_v23(b"TPE1", b"\x00\x00\x00\x10not-zlib-data!!!!",
                     flags=b"\x00\x80")
    path = _write_mp3(tmp_path, "zlib.mp3", bad, minor=3)
    _assert_loads_with_healthy_frame(path, b"TPE1")


def test_index_error_frame_is_skipped(tmp_path):
    """RVAD with a single data byte (needs at least two)."""
    bad = _frame_v23(b"RVAD", b"\x10")
    path = _write_mp3(tmp_path, "index.mp3", bad, minor=3)
    _assert_loads_with_healthy_frame(path, b"RVAD")


# ---------------------------------------------------------------------------
# Same four families, corrupt frame first (order independence)
# ---------------------------------------------------------------------------
def test_corrupt_frame_before_healthy_frame(tmp_path):
    bad = _frame_v24(b"TXXX", b"\x01\xff\xfe\x00\xd8\x00\xd8")
    path = _write_mp3(tmp_path, "decode-first.mp3", bad, title_first=False)
    _assert_loads_with_healthy_frame(path, b"TXXX")


# ---------------------------------------------------------------------------
# Other escaped crash sites found in the same audit
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fid, data, minor", [
    (b"APIC", b"", 4),                                        # empty APIC
    (b"APIC", b"\xffimage/png\x00\x03\x00img", 4),            # bad encoding byte
    (b"APIC", b"\x00-->\x00\x03\x00http://\xff", 4),          # non-ascii URL
    (b"WXXX", b"", 4),                                        # empty WXXX
    (b"WXXX", b"\xffdesc\x00http://x", 4),                    # bad encoding byte
    (b"GEOB", b"\xfftext/plain\x00", 4),                      # bad encoding byte
    (b"USER", b"", 4),                                        # empty USER
    (b"USER", b"\xffengabc", 4),                              # bad encoding byte
    (b"RVA2", b"abc", 4),                                     # no NUL terminator
    (b"RVA2", b"id\x00\x09\x00\x00", 4),                      # channel type OOB
    (b"RVA2", b"id\x00\x01", 4),                              # truncated adjustment
    (b"CTOC", b"", 4),                                        # empty CTOC
    (b"CTOC", b"el\x00", 4),                                  # no flags/count
    (b"CTOC", b"el\x00\x40\x00TIT2", 4),                      # truncated embedded TIT2
])
def test_other_malformed_frames_are_skipped(tmp_path, fid, data, minor):
    # Some damage is recoverable and is degraded, not dropped:
    #   * an unrecognized encoding byte is coerced to latin1 by the encoding
    #     setter;
    #   * a non-ascii APIC URL is kept as raw bytes (no decode is attempted);
    #   * a CTOC whose embedded description is corrupt keeps its TOC entries.
    # Everything else below is structurally unrecoverable and must be skipped.
    degraded = (
        (fid in (b"APIC", b"WXXX", b"GEOB", b"USER") and data[:1] == b"\xff")
        or (fid == b"APIC" and data.startswith(b"\x00-->"))
        or (fid == b"CTOC" and data.startswith(b"el\x00\x40\x00TIT2"))
    )
    blob = _frame_v24(fid, data)
    path = _write_mp3(tmp_path, "other-%s.mp3" % fid.decode(), blob, minor=minor)
    if degraded:
        _assert_loads(path)
    else:
        _assert_loads_with_healthy_frame(path, fid)


def test_truncated_frame_header_is_tolerated(tmp_path):
    """A frame header cut off mid-way (declared tag size runs past EOF) used to
    raise IndexError from the flag-bit accessors."""
    good = _healthy_title(4)
    partial = b"TPE1" + _syncsafe(100) + b"\x00"  # only 1 of 2 flag bytes
    blob = good + partial
    with open(AUDIO_PATH, "rb") as f:
        audio = f.read()
    # Declare more bytes than the buffer holds so size_left never reaches 0
    # before the partial header is read.
    tag = b"ID3\x04\x00\x00" + _syncsafe(len(blob) + 200) + blob
    path = tmp_path / "truncated-header.mp3"
    path.write_bytes(tag + audio)

    audio_file = eyed3.load(str(path))
    assert audio_file is not None
    assert audio_file.tag.title == "Healthy"


def test_chapter_kept_when_embedded_frame_corrupt(tmp_path):
    """A corrupt sub-frame must not discard the whole CHAP frame."""
    sub = _frame_v24(b"TIT2", b"\x03chapter") + _frame_v24(b"APIC", b"\x00image/jpeg\x00")
    chap_body = b"ch1\x00" + b"\x00" * 8 + b"\xff" * 8 + sub
    bad = _frame_v24(b"CHAP", chap_body)
    path = _write_mp3(tmp_path, "chap.mp3", bad)

    audio = eyed3.load(str(path))
    assert audio is not None
    assert audio.tag.title == "Healthy"
    assert len(audio.tag.chapters) == 1
    # The corrupt embedded APIC is gone; the healthy embedded TIT2 remains.
    chapter = audio.tag.chapters[b"ch1"]
    assert b"APIC" not in chapter.sub_frames
    assert chapter.title == "chapter"


def test_toc_kept_when_embedded_description_corrupt(tmp_path):
    """A corrupt embedded TIT2 description must not discard the whole CTOC."""
    bad_desc = _frame_v24(b"TIT2", b"\x01\xff\xfe\x00\xd8")
    toc_body = b"toc\x00\x40\x01ch1\x00" + bad_desc
    bad = _frame_v24(b"CTOC", toc_body)
    path = _write_mp3(tmp_path, "ctoc.mp3", bad)

    audio = eyed3.load(str(path))
    assert audio is not None
    assert audio.tag.title == "Healthy"
    assert len(audio.tag.table_of_contents) == 1


# ---------------------------------------------------------------------------
# The failure is reported through the library's own frame-exception family
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fid, data, version", [
    (b"TXXX", b"\x01\xff\xfe\x00\xd8\x00\xd8", ID3_V2_4),   # UnicodeDecodeError
    (b"APIC", b"\x00image/jpeg\x00", ID3_V2_4),             # TypeError
    (b"RVAD", b"\x10", ID3_V2_3),                           # IndexError
    (b"RVA2", b"id\x00\x09\x00\x00", ID3_V2_4),             # ValueError
    (b"CTOC", b"el\x00", ID3_V2_4),                         # IndexError (no flags)
])
def test_corruption_raises_frame_exception(fid, data, version):
    """Every audited crash site now surfaces as FrameException -- the exception
    type FrameSet.parse already treats as "skip this frame, keep loading"."""
    tag_header = TagHeader(version=version)
    frame_header = FrameHeader(fid, version)
    frame_header.data_size = len(data)
    with pytest.raises(FrameException):
        createFrame(tag_header, frame_header, data)


def test_compressed_garbage_raises_frame_exception():
    """The zlib.error path (compression flag + bogus payload) is also folded
    into FrameException."""
    version = ID3_V2_3
    tag_header = TagHeader(version=version)
    frame_header = FrameHeader(b"TPE1", version)
    frame_header.compressed = 1
    data = b"\x00\x00\x00\x10not-zlib-data!!!!"
    frame_header.data_size = len(data)
    with pytest.raises(FrameException):
        createFrame(tag_header, frame_header, data)
