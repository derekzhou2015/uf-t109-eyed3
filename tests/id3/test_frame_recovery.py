"""Regression tests for corrupt-frame handling.

A single corrupt frame must never end the read of the whole tag/file: the
bad frame is dropped, normalized into the library's frame-level error type
(:class:`eyed3.id3.frames.FrameException`), and every other frame is still
parsed.  These cases are built deterministically from bytes so the expected
outcome is pinned, independent of any file in ``tests/data``.
"""
import logging
import zlib

import pytest

import eyed3
from eyed3.id3 import ID3_V2_3, ID3_V2_4
from eyed3.id3.frames import FrameException, FrameHeader, createFrame
from eyed3.id3.headers import TagHeader

# A valid MPEG-1 Layer III, 128 kb/s, 44.1 kHz frame (417 bytes).
_MPEG_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _frame(fid, data, version=ID3_V2_4):
    """Render a complete ID3v2 frame (header + data)."""
    return FrameHeader(fid, version).render(len(data)) + data


def _flagged_frame(fid, data, version=ID3_V2_4, compressed=False,
                   data_length_indicator=False):
    """Render a frame with header flags and any pre-data flag bytes."""
    header = FrameHeader(fid, version)
    header.compressed = compressed
    header.data_length_indicator = data_length_indicator
    prefix = b""
    if data_length_indicator:
        # 4 byte, sync-safe encoded length of the (decompressed) data.
        prefix = len(data).to_bytes(4, "big")
    return header.render(len(prefix) + len(data)) + prefix + data


def _mp3(tag_body, version=4, audio=_MPEG_FRAME * 4):
    """Build an mp3 byte string: ID3v2 tag (``tag_body``) followed by audio."""
    size = len(tag_body)
    header = b"ID3" + bytes([version, 0, 0]) + bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F,
        (size >> 7) & 0x7F, size & 0x7F])
    return header + tag_body + audio


def _write(tmp_path, body, version=4, name="corrupt.mp3", audio=_MPEG_FRAME * 4):
    path = tmp_path / name
    path.write_bytes(_mp3(body, version=version, audio=audio))
    return path


GOOD_BEFORE = _frame(b"TIT2", b"\x03Title")
GOOD_AFTER = _frame(b"TPE1", b"\x03Artist")

# (name, corrupt frame bytes, its frame id)
CORRUPT_FRAMES = [
    # APIC missing its picture-type byte: ord(b"") used to raise TypeError.
    ("apic_short", _frame(b"APIC", b"\x00image/jpeg\x00"), b"APIC"),
    # Unsupported text encoding byte: used to raise NotImplementedError.
    ("apic_bad_encoding", _frame(b"APIC", b"\x09image/jpeg\x00\x03d\x00xx"),
     b"APIC"),
    ("geob_bad_encoding", _frame(b"GEOB", b"\xbfH\xaa\x0b\xae8"), b"GEOB"),
    ("user_bad_encoding", _frame(b"USER", b"_<w\xa7\x9d\x02"), b"USER"),
    ("wxxx_bad_encoding", _frame(b"WXXX", b"B\xc2"), b"WXXX"),
    # RVA2 channel type out of range: used to raise ValueError.
    ("rva2_bad_channel", _frame(b"RVA2", b"id\x00\x09\x00\x00"), b"RVA2"),
    # RVAD with a single data byte: used to raise IndexError.
    ("rvad_short", _frame(b"RVAD", b"K", ID3_V2_3), b"RVAD"),
    # Compressed frame whose payload is not zlib data: used to raise
    # zlib.error out of Frame._disassembleFrame.
    ("compressed_junk",
     _flagged_frame(b"TIT2", b"JUNK", compressed=True,
                    data_length_indicator=True), b"TIT2"),
]


@pytest.mark.parametrize("name,bad_frame,expected_id", CORRUPT_FRAMES,
                         ids=[c[0] for c in CORRUPT_FRAMES])
def test_corrupt_frame_does_not_end_tag_read(tmp_path, caplog, name, bad_frame,
                                             expected_id):
    """One bad frame loses only itself; frames before/after survive."""
    path = _write(tmp_path, GOOD_BEFORE + bad_frame + GOOD_AFTER,
                  name=name + ".mp3")

    with caplog.at_level(logging.WARNING, logger="eyed3.id3.frames"):
        audio_file = eyed3.load(path)

    assert audio_file is not None
    tag = audio_file.tag
    assert tag is not None

    # The frames around the corrupt one are intact.
    assert tag.getTextFrame(b"TIT2") == "Title"
    assert tag.getTextFrame(b"TPE1") == "Artist"

    # The corrupt frame is accounted for at the frame level...
    assert expected_id in tag.frame_set.corrupt_frame_ids
    # ...and is not confused with a well-formed but unsupported frame.
    assert expected_id not in tag.frame_set.unknown_frame_ids
    # ...nor is a partial/bogus frame object exposed.
    if expected_id not in (b"TIT2", b"TPE1"):
        assert tag.frame_set[expected_id] is None

    # It was reported once, in the library's own frame-error idiom.
    frame_errors = [r.getMessage() for r in caplog.records
                    if "Frame error" in r.getMessage()]
    assert len(frame_errors) == 1, frame_errors


def test_corrupt_frame_does_not_lose_good_frame_of_same_id(tmp_path):
    """A good frame sharing an ID with a corrupt one is still kept."""
    corrupt_tit2 = _flagged_frame(b"TIT2", b"JUNK", compressed=True,
                                  data_length_indicator=True)
    body = GOOD_BEFORE + corrupt_tit2 + _frame(b"TIT2", b"\x03Second")
    path = _write(tmp_path, body)

    audio_file = eyed3.load(path)
    texts = sorted(f.text for f in audio_file.tag.frame_set[b"TIT2"])
    assert texts == ["Second", "Title"]
    assert b"TIT2" in audio_file.tag.frame_set.corrupt_frame_ids


def test_corrupt_frame_in_v23_tag(tmp_path):
    body = (_frame(b"TIT2", b"\x03Title", ID3_V2_3) +
            _frame(b"APIC", b"\x00image/jpeg\x00", ID3_V2_3) +
            _frame(b"TPE1", b"\x03Artist", ID3_V2_3))
    path = _write(tmp_path, body, version=3)

    audio_file = eyed3.load(path)
    assert audio_file.tag.getTextFrame(b"TIT2") == "Title"
    assert audio_file.tag.getTextFrame(b"TPE1") == "Artist"
    assert b"APIC" in audio_file.tag.frame_set.corrupt_frame_ids


def test_corrupt_frame_ids_reset_between_parses(tmp_path):
    """The corrupt-frame ledger reflects only the most recent parse."""
    path = _write(tmp_path, GOOD_BEFORE + _frame(b"APIC", b"\x00"))
    assert b"APIC" in eyed3.load(path).tag.frame_set.corrupt_frame_ids

    clean = _write(tmp_path, GOOD_BEFORE, name="clean.mp3")
    assert eyed3.load(clean).tag.frame_set.corrupt_frame_ids == set()


def test_truncated_frame_data_is_tolerated(tmp_path):
    """A frame whose declared size overruns the tag must not crash the read."""
    header = FrameHeader(b"APIC", ID3_V2_4)
    body = GOOD_BEFORE + header.render(0xFFFF) + b"\x00image/jpeg\x00"
    path = _write(tmp_path, body)

    audio_file = eyed3.load(path)
    assert audio_file.tag.getTextFrame(b"TIT2") == "Title"
    assert b"APIC" in audio_file.tag.frame_set.corrupt_frame_ids


def test_tag_size_smaller_than_extended_header(tmp_path):
    """A malformed envelope is treated as an empty frame area, not a crash."""
    # v2.3 tag, extended-header flag set, extended header claims 200 bytes
    # while the tag itself only reports 20.
    body = (b"\x00\x00\x00\xc8" + b"\x00\x00" + b"\x00\x00\x00\x00" +
            b"\x00" * 12)
    size = len(body)
    blob = (b"ID3" + bytes([3, 0, 0x40]) + bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F,
        (size >> 7) & 0x7F, size & 0x7F]) + body + _MPEG_FRAME * 4)
    path = tmp_path / "ext_overrun.mp3"
    path.write_bytes(blob)

    audio_file = eyed3.load(path)
    assert audio_file is not None
    assert audio_file.tag is not None
    assert len(audio_file.tag.frame_set) == 0


@pytest.mark.parametrize("exc", [TypeError("bad"), ValueError("bad"),
                                 zlib.error("bad"), IndexError("bad"),
                                 NotImplementedError("bad"),
                                 UnicodeDecodeError("utf-16", b"x", 0, 1, "x")])
def test_frame_parse_error_normalizes_to_frame_exception(exc):
    from eyed3.id3.frames import _frameParseError

    err = _frameParseError(b"APIC", exc)
    assert isinstance(err, FrameException)
    assert err.__cause__ is exc
    assert "APIC" in str(err)
    assert type(exc).__name__ in str(err)


def test_frame_parse_error_preserves_frame_exception():
    from eyed3.id3.frames import _frameParseError

    original = FrameException("Invalid v2.4 frame: RVAD")
    assert _frameParseError(b"RVAD", original) is original


def test_create_frame_still_raises_own_exception_directly():
    """Individual frame classes keep their own (documented) error types.

    Normalization happens at the FrameSet boundary, not inside createFrame,
    so direct callers of the frame classes are unaffected.
    """
    header = FrameHeader(b"RVA2", ID3_V2_4)
    with pytest.raises(ValueError):
        createFrame(TagHeader(ID3_V2_4), header, b"id\x00\x09\x00\x00")


# ---------------------------------------------------------------------------
# Existing leniency must not narrow because of the corrupt-frame handling.
# These cases never raised; they must still be accepted (not dropped).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,frame_bytes,check", [
    # Invalid date text: repaired to empty, frame kept.
    ("bad_date", _frame(b"TDRC", b"\x031234:12"),
     lambda fs: fs[b"TDRC"] is not None and fs[b"TDRC"][0].text == ""),
    # Unknown text encoding byte: coerced to latin1, frame kept.
    ("bad_text_encoding", _frame(b"TXXX", b"\x07d\x00text"),
     lambda fs: fs[b"TXXX"] is not None),
    # APIC with an out-of-range picture type: coerced to OTHER, frame kept.
    ("bad_apic_type", _frame(b"APIC", b"\x00image/jpeg\x00\xffd\x00xx"),
     lambda fs: fs[b"APIC"] is not None
     and fs[b"APIC"][0].picture_type == 0),
    # Well-formed but unsupported frame: kept as unknown, not corrupt.
    ("unknown_frame", _frame(b"ASDF", b"whatever"),
     lambda fs: fs[b"ASDF"] is not None
     and b"ASDF" in fs.unknown_frame_ids
     and b"ASDF" not in fs.corrupt_frame_ids),
], ids=["bad_date", "bad_text_encoding", "bad_apic_type", "unknown_frame"])
def test_existing_leniency_is_not_narrowed(tmp_path, name, frame_bytes, check):
    path = _write(tmp_path, GOOD_BEFORE + frame_bytes + GOOD_AFTER,
                  name=name + ".mp3")

    audio_file = eyed3.load(path)
    frame_set = audio_file.tag.frame_set

    # The tolerated frame is still present and the surrounding frames intact.
    assert check(frame_set), name
    assert frame_set[b"TIT2"] is not None
    assert frame_set[b"TPE1"] is not None
    # And nothing was misclassified as corrupt.
    assert not frame_set.corrupt_frame_ids


def test_frame_with_empty_data_is_still_accepted(tmp_path):
    """A well-formed frame carrying zero data is not "corrupt"."""
    body = GOOD_BEFORE + _frame(b"TPE1", b"") + GOOD_AFTER
    path = _write(tmp_path, body)

    frame_set = eyed3.load(path).tag.frame_set
    assert b"TPE1" in frame_set
    assert frame_set.corrupt_frame_ids == set()
