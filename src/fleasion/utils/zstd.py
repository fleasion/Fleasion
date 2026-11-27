"""Bounded Zstandard helpers built on the standard library ``compression.zstd``."""

from __future__ import annotations

from compression.zstd import ZstdCompressor, ZstdDecompressor, ZstdDict, ZstdError

__all__ = [
    'ZSTD_MAGIC',
    'ZstdError',
    'zstd_compress_with_raw_dictionary',
    'zstd_decompress_frame',
]

# The four bytes that open every Zstandard frame
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


def zstd_decompress_frame(
    data: bytes,
    *,
    max_size: int,
    raw_dictionary: bytes | None = None,
) -> bytes:
    """Decompress the first Zstandard frame in `data`, refusing oversized output.

    Bytes after the first frame are ignored, matching how Roblox payloads are
    read elsewhere. Frames without a declared content size are still accepted.

    Parameters
    ----------
    data : bytes
        Compressed bytes starting with a Zstandard frame.
    max_size : int
        Largest decompressed size accepted, in bytes.
    raw_dictionary : bytes or None, optional
        Raw-content dictionary the frame was compressed against.

    Returns
    -------
    bytes
        The decompressed frame.

    Raises
    ------
    ZstdError
        If the frame is malformed, truncated or decompresses past `max_size`.
    """
    zstd_dict = None if raw_dictionary is None else ZstdDict(raw_dictionary, is_raw=True)
    decompressor = ZstdDecompressor(zstd_dict)
    output = decompressor.decompress(data, max_length=max_size)

    # Output stops at max_size without raising, so an unfinished frame means it
    # was either cut short or larger than allowed
    if not decompressor.eof:
        msg = f'Zstandard frame is truncated or larger than {max_size} bytes'
        raise ZstdError(msg)
    return output


def zstd_compress_with_raw_dictionary(data: bytes, raw_dictionary: bytes) -> bytes:
    """Compress ``data`` into one Zstandard frame against a raw-content dictionary.

    Parameters
    ----------
    data : bytes
        Bytes to compress.
    raw_dictionary : bytes
        Raw-content dictionary shared with the reader.

    Returns
    -------
    bytes
        A complete Zstandard frame.
    """
    compressor = ZstdCompressor(zstd_dict=ZstdDict(raw_dictionary, is_raw=True))
    return compressor.compress(data, mode=ZstdCompressor.FLUSH_FRAME)
