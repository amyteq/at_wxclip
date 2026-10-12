"""The XOR layer, with the three sizes that matter: under, exactly, and over 128 KiB."""

import pytest

from wx_dlh.decrypt import (
    HEAD_SAMPLE,
    HeadDecryptor,
    decrypt_file,
    decrypt_in_place,
    looks_like_mp4,
)
from wx_dlh.keystream import HEAD_BYTES, KeystreamError, keystream

KEY = "1234567890"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00"


def plaintext(size: int) -> bytes:
    """Something recognisable, starting with a real mp4 box header."""
    body = bytes(range(256)) * (size // 256 + 1)
    return (MP4_HEAD + body)[:size]


def ciphertext(plain: bytes, key: str = KEY) -> bytes:
    """What the CDN serves: the head XORed, everything after it untouched."""
    stream = keystream(key)
    return bytes(b ^ (stream[i] if i < HEAD_BYTES else 0) for i, b in enumerate(plain))


#: Every boundary worth naming, plus one that is not a multiple of anything.
SIZES = [0, 1, 4096, HEAD_BYTES - 1, HEAD_BYTES, HEAD_BYTES + 1, HEAD_BYTES * 2 + 7]


@pytest.mark.parametrize("size", SIZES)
def test_decrypts_whatever_the_file_size(size):
    plain = plaintext(size)
    assert HeadDecryptor(KEY).update(ciphertext(plain)) == plain


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("chunk", [1, 7, 4096, HEAD_BYTES, HEAD_BYTES + 3])
def test_chunking_never_changes_the_result(size, chunk):
    """Chunks arrive at whatever size the socket feels like, including across the 128 KiB
    line, and the keystream has to stay lined up with the file rather than the chunk."""
    plain = plaintext(size)
    cipher = ciphertext(plain)
    decryptor = HeadDecryptor(KEY)
    out = b"".join(decryptor.update(cipher[at : at + chunk]) for at in range(0, size, chunk))
    assert out == plain
    assert decryptor.offset == size


@pytest.mark.parametrize("offset", [0, 1, 4095, HEAD_BYTES - 1, HEAD_BYTES, HEAD_BYTES + 1])
def test_resuming_at_an_offset_picks_the_keystream_up_there(offset):
    """What a resumed download does: the bytes before `offset` are already decrypted on
    disk, and the rest has to be XORed against the keystream at its real file position."""
    plain = plaintext(HEAD_BYTES + 5000)
    cipher = ciphertext(plain)
    assert HeadDecryptor(KEY, offset=offset).update(cipher[offset:]) == plain[offset:]


def test_past_the_head_nothing_is_touched_and_no_keystream_is_generated():
    decryptor = HeadDecryptor(KEY, offset=HEAD_BYTES)
    assert decryptor.done
    assert decryptor.update(b"already in the clear") == b"already in the clear"


def test_done_flips_exactly_at_the_boundary():
    decryptor = HeadDecryptor(KEY)
    assert not decryptor.done
    decryptor.update(b"\x00" * (HEAD_BYTES - 1))
    assert not decryptor.done
    decryptor.update(b"\x00")
    assert decryptor.done


def test_xor_is_its_own_inverse():
    plain = plaintext(HEAD_BYTES + 10)
    assert HeadDecryptor(KEY).update(HeadDecryptor(KEY).update(plain)) == plain


def test_a_negative_offset_is_a_bug_worth_raising_on():
    with pytest.raises(ValueError):
        HeadDecryptor(KEY, offset=-1)


def test_a_bad_key_never_reaches_the_data():
    with pytest.raises(KeystreamError):
        HeadDecryptor("1.23e+19")


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (MP4_HEAD, True),
        (b"\x00\x00\x00\x20ftyq" + b"\x00" * 8, False),
        (b"", False),
        (b"short", False),
    ],
)
def test_looks_like_mp4_recognises_the_ftyp_box(head, expected):
    assert looks_like_mp4(head) is expected


def test_decrypt_file_writes_a_playable_copy_and_leaves_the_source_alone(tmp_path):
    plain = plaintext(HEAD_BYTES + 321)
    src = tmp_path / "raw.bin"
    src.write_bytes(ciphertext(plain))
    result = decrypt_file(src, tmp_path / "out" / "clip.mp4", KEY)
    assert result.path.read_bytes() == plain
    assert src.read_bytes() == ciphertext(plain)
    assert result.size == len(plain)
    assert result.decrypted == HEAD_BYTES
    assert result.head_after == plain[:HEAD_SAMPLE]
    assert result.looks_like_mp4
    assert "mp4" in result.summary()


def test_decrypt_file_reports_a_short_file_as_partly_decrypted(tmp_path):
    plain = plaintext(1000)
    src = tmp_path / "small.bin"
    src.write_bytes(ciphertext(plain))
    result = decrypt_file(src, tmp_path / "small.mp4", KEY)
    assert result.decrypted == 1000
    assert result.path.read_bytes() == plain


def test_decrypt_file_refuses_to_write_over_its_own_source(tmp_path):
    src = tmp_path / "same.bin"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="in_place"):
        decrypt_file(src, src, KEY)


def test_decrypt_in_place_rewrites_only_the_head(tmp_path):
    plain = plaintext(HEAD_BYTES + 4096)
    path = tmp_path / "clip.mp4"
    path.write_bytes(ciphertext(plain))
    result = decrypt_in_place(path, KEY)
    assert path.read_bytes() == plain
    assert result.size == len(plain)
    assert result.looks_like_mp4


def test_a_wrong_key_says_so_instead_of_pretending(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(ciphertext(plaintext(HEAD_BYTES + 8)))
    result = decrypt_in_place(path, "999")
    assert not result.looks_like_mp4
    assert "wrong decode_key" in result.summary()
