# -*- coding: utf-8 -*-
"""Regression tests: a single corrupt frame must not abort loading the file.

Covers the four corruption forms observed in the wild, each of which used to
escape frame parsing as a native exception and kill the whole file load:

- UnicodeDecodeError  (undecodable text payload, e.g. TXXX with bad UTF-16)
- TypeError           (truncated APIC, missing picture-type byte)
- zlib.error          (v2.3 compressed frame with non-zlib payload)
- IndexError          (truncated RVAD payload)

The required behavior: the failure is normalized to
``eyed3.id3.frames.FrameException`` (the library's frame exception family),
the corrupt frame is skipped, and the remaining healthy frames survive.
"""
import pytest

from io import BytesIO
from pathlib import Path

import eyed3
from eyed3.id3 import ID3_V2_3, ID3_V2_4
from eyed3.id3.frames import createFrame, FrameException
from eyed3.id3.headers import FrameHeader, TagHeader
from .. import DATA_D


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F,
                  (n >> 7) & 0x7F, n & 0x7F])


def _frame_v23(fid, data, flags=b"\x00\x00"):
    # v2.3 frame sizes are plain 32-bit big-endian (not syncsafe)
    return fid + len(data).to_bytes(4, "big") + flags + data


def _frame_v24(fid, data, flags=b"\x00\x00"):
    return fid + _syncsafe(len(data)) + flags + data


# A healthy title frame for each tag version.
GOOD_FRAME = {3: _frame_v23(b"TIT2", b"\x03Healthy"),
              4: _frame_v24(b"TIT2", b"\x03Healthy")}

# (corruption form, tag minor version, corrupt frame bytes)
CORRUPT_FRAMES = [
    # TXXX with a UTF-16 payload of lone surrogates -> UnicodeDecodeError
    ("UnicodeDecodeError", 4, _frame_v24(b"TXXX", b"\x01\xff\xfe\x00\xd8\x00\xd8")),
    # APIC missing the picture-type byte -> TypeError from ord(b"")
    ("TypeError", 4, _frame_v24(b"APIC", b"\x03image/jpeg\x00")),
    # v2.3 compression flag set but payload is not zlib data -> zlib.error
    ("zlib.error", 3, _frame_v23(b"TIT2", b"\x00\x00\x00\x20not-zlib-data",
                                 flags=b"\x00\x80")),
    # RVAD truncated to a single byte -> IndexError
    ("IndexError", 3, _frame_v23(b"RVAD", b"\x10")),
]


def _corrupt_frame_args(frame_bytes, minor_version):
    """Return (tag_header, frame_header, data) for a corrupt frame blob."""
    version = ID3_V2_4 if minor_version == 4 else ID3_V2_3
    tag_header = TagHeader(version)
    frame_header = FrameHeader.parse(BytesIO(frame_bytes), version)
    data = frame_bytes[frame_header.size:][:frame_header.data_size]
    return tag_header, frame_header, data


@pytest.mark.parametrize("form,minor_version,frame_bytes", CORRUPT_FRAMES,
                         ids=[form for form, _, _ in CORRUPT_FRAMES])
def test_corrupt_frame_raises_frame_exception(form, minor_version, frame_bytes):
    """Frame-level corruption is normalized into the FrameException family."""
    tag_header, frame_header, data = _corrupt_frame_args(frame_bytes,
                                                         minor_version)
    with pytest.raises(FrameException):
        createFrame(tag_header, frame_header, data)


@pytest.mark.skipif(not Path(DATA_D).exists(), reason="test requires data files")
@pytest.mark.parametrize("form,minor_version,frame_bytes", CORRUPT_FRAMES,
                         ids=[form for form, _, _ in CORRUPT_FRAMES])
def test_corrupt_frame_does_not_abort_file_load(form, minor_version,
                                                frame_bytes, tmpdir):
    """A single corrupt frame is skipped; the rest of the tag/file loads."""
    good = GOOD_FRAME[minor_version]
    blob = good + frame_bytes
    tag = (b"ID3" + bytes([minor_version, 0, 0]) + _syncsafe(len(blob)) + blob)

    testfile = Path(str(tmpdir)) / "corrupt-{}.mp3".format(form)
    with open(Path(DATA_D) / "notag-vbr.mp3", "rb") as f:
        testfile.write_bytes(tag + f.read())

    audio_file = eyed3.load(str(testfile))
    assert audio_file is not None
    assert audio_file.tag is not None
    # The healthy frame survived the corrupt neighbor.
    assert audio_file.tag.title == "Healthy"
