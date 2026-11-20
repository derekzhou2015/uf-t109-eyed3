##########
Compliance
##########


ID3
===

Unsupported Features
--------------------
* ID3 frame encryption
* Writing of sync-safe data (i.e. unsynchronized) because it is 2012.
  Reading of unsyncronized tags (v2.3) and frames (v2.4) **is** supported.

Corrupt Data Tolerance
----------------------
Data read from files is arbitrary and may be damaged. eyeD3 confines such
damage to the smallest unit it can be attributed to, and never lets one bad
unit end the read of a larger one.

* **Frames.** A frame that cannot be parsed is dropped on its own. The failure
  is normalized to :class:`eyed3.id3.frames.FrameException` (the library's
  frame-level error type), logged as a warning, and the frame IDs involved are
  exposed as :attr:`eyed3.id3.frames.FrameSet.corrupt_frame_ids`. Frames
  before and after its position, including other frames sharing the same ID,
  are parsed normally. A corrupt frame is *not* reported as ``unknown`` (see
  :attr:`eyed3.id3.frames.FrameSet.unknown_frame_ids`); "unknown" means a
  well-formed frame eyeD3 has no class for, while "corrupt" means the bytes
  did not parse.

  This does not narrow existing tolerance: frames whose values are merely
  invalid (bad dates, unknown text encodings, empty picture types, unknown
  frame IDs, ...) continue to be accepted--possibly repaired with a warning--
  exactly as before. Only a frame whose parsing could not complete is dropped.

* **Tag envelope.** A malformed tag or extended header is reported as
  :class:`eyed3.id3.tag.TagException`. A tag whose declared size is smaller
  than its own extended header is treated as an empty frame area rather than
  a failure.

* **Audio data.** MPEG audio info is computed on a best-effort basis. A
  corrupt, truncated, or entirely absent MPEG frame yields
  ``AudioFile.info is None`` (with a warning); it does not prevent the tag
  from being read or the file from loading.

Batch callers (e.g. importing a library) therefore lose at most the corrupt
frame, and a file with a valid tag still loads even when its audio data is
damaged.


Dates
-----
One of the major differences between 2.3 and 2.4 is dates.

ID3 v2.3 Date Frames
~~~~~~~~~~~~~~~~~~~~
- TDAT date (recording date of form DDMM, always 4 bytes)
- TYER year (recording year of form YYYY, always 4 bytes)
- TIME time (recording time of form HHMM, always 4 bytes)
- TORY orig release year
- TRDA recording date (more freeform replacement for TDAT, TYER, TIME.
  e.g., "4th-7th June, 12th June" in combination with TYER)
- TDLY playlist delay (also defined in ID3 v2.4)

ID3 v2.4 Date Frames
~~~~~~~~~~~~~~~~~~~~
All v2.4 dates follow ISO 8601 formats.

- TDEN encoding datetime
- TDOR orig release date
- TDRC recording date
- TDRL release date
- TDTG tagging time
- TDLY playlist delay (also defined in ID3 v2.3)

From the ID3 specs::

    yyyy-MM-ddTHH:mm:ss (year, "-", month, "-", day, "T", hour (out of
    24), ":", minutes, ":", seconds), but the precision may be reduced by
    removing as many time indicators as wanted. Hence valid timestamps
    are yyyy, yyyy-MM, yyyy-MM-dd, yyyy-MM-ddTHH, yyyy-MM-ddTHH:mm
    and yyyy-MM-ddTHH:mm:ss. All time stamps are UTC. For
    durations, use the slash character as described in 8601, and for
    multiple non- contiguous dates, use multiple strings, if allowed
    by the frame definition.

The ISO 8601 'W' delimiter for numeric weeks is NOT supported.

Times that contain a 'Z' at the end to signal the time is UTC is supported.

Common Date Frame Extensions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
MusicBrainz uses *XDOR* in v2.3 tags as the **full** original release date,
whereas *TORY* (v2.3) only represents the release year. Version 2.4 does not 
use/need this extension since *TDOR* is available.

v2.4 <-> 2.3 mappings
~~~~~~~~~~~~~~~~~~~~~
When converting to/from v2.3 and v2.4 it is neceswsary to convert date frames.
The following is the mappings eyeD3 uses when converting.

Version 2.3 --> version 2.4

* TYER, TDAT, TIME --> TDRC
* TORY             --> TDOR
* TRDA             --> none
* XDOR             --> TDOR

If both *TORY* and *XDOR* exist, XDOR is preferred.

Version 2.4 --> version 2.3

* TDRC --> TYER, TDAT, TIME
* TDOR --> TORY
* TDRL --> TORY
* TDEN --> none
* TDTG --> none

Non Standard Frame Support
--------------------------

NCON
~~~~
A MusicMatch extension of unknown binary format. Frames of this type are
parsed as raw ``Frame`` objects, therefore the data is not parsed. The frames
are preserved and can be deleted and written (as is).

TCMP
~~~~
An iTunes extension to signify that a track is part of a compilation.
This frame is handled by ``TextFrame`` and the data is either a '1' if
part of a compilation or '0' (or empty) if not.

XSOA, XSOP, XSOT
~~~~~~~~~~~~~~~~
These are alternative sort-order strings for album, performer, and title,
respectively. They are often added to ID3v2.3 tags while v2.4 does not
require them since TSOA, TSOP, and TSOT are native frames.

These frames are preserved but are not written when using v2.3. If the
tag is converted to v2.4 then the corresponding native frame is used.

XDOR
~~~~
A MusicBrainz extension for the **full** original release date, since TORY
only contains the year of original release.  In ID3 v2.4 this frame became
TDOR.

PCST, WFED, TKWD, TDES, TGID
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Apple extensions for podcasts.
