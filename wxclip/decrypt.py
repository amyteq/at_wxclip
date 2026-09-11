"""Undo the encryption on a downloaded file's head.

Only the first 128 KiB of a WeChat Channels download are encrypted, and only by XOR
against the ISAAC64 keystream of its decode_key. Everything past that offset is already
the media file, byte for byte - which is why a 585 MB video costs the same to decrypt as
a 4 MB one, and why a resumed download only has to care about the keystream until it gets
past HEAD_BYTES.

XOR being its own inverse, `decrypt` and `encrypt` are the same operation here; the names
in this module all say decrypt because that is the only direction wxclip travels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wxclip.keystream import HEAD_BYTES, keystream

#: Enough of the head to tell an mp4 from a still-encrypted mess: `....ftyp`.
HEAD_SAMPLE = 16

#: Copying a 585 MB file should not be done in 64 KiB nibbles.
CHUNK_SIZE = 1 << 20


def looks_like_mp4(head: bytes) -> bool:
    """True when the head carries an ISO base media file box. A wrong decode_key produces
    bytes just as random as the ciphertext, so this is the cheap "did it work" check."""
    return head[4:8] == b"ftyp"


class HeadDecryptor:
    """Applies the keystream to a stream of chunks, from `offset` bytes into the file.

    The offset is what makes a resumed download work: the bytes already on disk have been
    decrypted, and the next chunk has to line up with the keystream at the position it
    actually occupies in the file, not at zero.
    """

    def __init__(self, decode_key: str, *, offset: int = 0, head_bytes: int = HEAD_BYTES) -> None:
        if offset < 0:
            raise ValueError(f"offset must not be negative, got {offset}")
        self.head_bytes = head_bytes
        self.offset = offset
        self._keystream = keystream(decode_key, head_bytes) if offset < head_bytes else b""

    @property
    def done(self) -> bool:
        """Past the encrypted head - every further chunk passes through untouched."""
        return self.offset >= self.head_bytes

    def update(self, chunk: bytes) -> bytes:
        """Return `chunk` decrypted, and advance by its length."""
        start = self.offset
        self.offset += len(chunk)
        if start >= self.head_bytes:
            return chunk
        cut = min(len(chunk), self.head_bytes - start)
        key = self._keystream[start : start + cut]
        head = bytes(b ^ k for b, k in zip(chunk[:cut], key, strict=True))
        return head + chunk[cut:] if cut < len(chunk) else head


@dataclass(frozen=True)
class DecryptResult:
    """What a decrypt run touched, and whether the result looks like a video."""

    path: Path
    size: int
    #: Bytes actually XORed - less than HEAD_BYTES only for a file shorter than the head.
    decrypted: int
    head_before: bytes
    head_after: bytes

    @property
    def looks_like_mp4(self) -> bool:
        return looks_like_mp4(self.head_after)

    def summary(self) -> str:
        verdict = "mp4" if self.looks_like_mp4 else "not an mp4 header - wrong decode_key?"
        return (
            f"{self.path}: {self.size} bytes, decrypted the first {self.decrypted}\n"
            f"  head {self.head_before.hex()} -> {self.head_after.hex()} ({verdict})"
        )


def decrypt_file(
    src: Path,
    dst: Path,
    decode_key: str,
    *,
    head_bytes: int = HEAD_BYTES,
    chunk_size: int = CHUNK_SIZE,
) -> DecryptResult:
    """Stream `src` to `dst`, decrypting the head on the way. `src` is left alone."""
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError(f"source and destination are the same file: {src} - use in_place")
    decryptor = HeadDecryptor(decode_key, head_bytes=head_bytes)
    before = after = b""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fh, dst.open("wb") as out:
        while chunk := fh.read(chunk_size):
            plain = decryptor.update(chunk)
            if not before:
                before, after = chunk[:HEAD_SAMPLE], plain[:HEAD_SAMPLE]
            out.write(plain)
    size = decryptor.offset
    return DecryptResult(dst, size, min(size, head_bytes), before, after)


def decrypt_in_place(
    path: Path,
    decode_key: str,
    *,
    head_bytes: int = HEAD_BYTES,
) -> DecryptResult:
    """Rewrite only the encrypted head of `path`. The rest of the file is never read,
    so this costs the same whether the file is 4 MB or 585 MB."""
    path = Path(path)
    size = path.stat().st_size
    decryptor = HeadDecryptor(decode_key, head_bytes=head_bytes)
    with path.open("r+b") as fh:
        head = fh.read(head_bytes)
        plain = decryptor.update(head)
        fh.seek(0)
        fh.write(plain)
    return DecryptResult(path, size, len(head), head[:HEAD_SAMPLE], plain[:HEAD_SAMPLE])
