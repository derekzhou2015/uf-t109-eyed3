"""Regression tests for corrupt MPEG audio data.

Audio info is best effort: a corrupt/truncated MPEG frame, or a file too
short to hold one, must not make a file whose tag parsed fine unreadable.
The file is still returned with ``info is None``.
"""
import pytest

import eyed3

_MPEG_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _mp3(tag_body, audio):
    size = len(tag_body)
    return (b"ID3" + bytes([4, 0, 0]) + bytes([
        (size >> 21) & 0x7F, (size >> 14) & 0x7F,
        (size >> 7) & 0x7F, size & 0x7F]) + tag_body + audio)


def _tag_body():
    from eyed3.id3 import ID3_V2_4
    from eyed3.id3.frames import FrameHeader

    data = b"\x03Title"
    return FrameHeader(b"TIT2", ID3_V2_4).render(len(data)) + data


@pytest.mark.parametrize("audio", [
    b"",                                   # no audio at all
    b"\xff\xfb\x90\x64",                  # only a frame header
    b"\xff\xfb\x90\x64" + b"\x00" * 10,   # truncated first frame
    b"\xff\xfb\x90\x64" + b"\x00" * 20 + b"Xing",  # truncated VBR header
    b"\x00\x01\x02\x03" * 100,            # no sync at all
], ids=["empty", "header_only", "truncated_frame", "truncated_xing", "no_sync"])
def test_corrupt_audio_info_is_best_effort(tmp_path, audio):
    path = tmp_path / "audio.mp3"
    path.write_bytes(_mp3(_tag_body(), audio))

    audio_file = eyed3.load(path)
    assert audio_file is not None
    # The tag still parses; only the audio info is unavailable.
    assert audio_file.tag is not None
    assert audio_file.tag.getTextFrame(b"TIT2") == "Title"
    assert audio_file.info is None


def test_valid_audio_still_produces_info(tmp_path):
    path = tmp_path / "ok.mp3"
    path.write_bytes(_mp3(_tag_body(), _MPEG_FRAME * 4))

    audio_file = eyed3.load(path)
    assert audio_file.info is not None
    assert audio_file.info.time_secs > 0
