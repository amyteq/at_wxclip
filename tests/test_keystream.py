"""The Python ISAAC64 port, against keystreams taken from the reference implementation.

The five hashes below are `evals/README.md`'s golden table, produced by running
`electron/decrypt.js` from `harrysun2006/WeChatVideoDownloader` under Node v24. If this
file goes red, the port has drifted from what actually decrypts a WeChat download - not
the other way round.
"""

import hashlib

import pytest

from wx_dlh.keystream import HEAD_BYTES, Isaac64, KeystreamError, keystream, parse_decode_key

#: seed (as the decimal string it must stay) -> sha256 of its 131072-byte keystream.
GOLDEN = {
    "0": "e1662af3b7e59867c919ad19055fc8cecea2b154d37e1459b2f96d1da14cef1f",
    "1": "39d98f5b25cc52f0996f7ef9e1022156cb029901be6a11ebbdd7469c6ffd5839",
    "1234567890": "62c16e2c25857dfef81231038222c63d5fef01f03b9b74779475a5576e051efb",
    "13835058055282163712": "da37f3ac8f8d2699aded0c966d7cff55e2b63be2798db981c740d0d7a9fd5195",
    "18446744073709551615": "5afbffd76305e81467f97c6370fa07916e9b197611bf5c357a854eb92a6354a1",
}


@pytest.mark.parametrize(("seed", "digest"), sorted(GOLDEN.items()))
def test_matches_the_keystream_decrypt_js_produces(seed, digest):
    stream = keystream(seed)
    assert len(stream) == HEAD_BYTES
    assert hashlib.sha256(stream).hexdigest() == digest


def test_head_bytes_is_the_128_kib_the_format_encrypts():
    assert HEAD_BYTES == 131072


def test_first_words_are_the_published_isaac64_sequence():
    """Seed 0 has a well-known output; getting the result array's order backwards would
    still look random, so pin the first words rather than only the hash."""
    generator = Isaac64(0)
    assert [generator.next() for _ in range(3)] == [
        0x9D39247E33776D41,
        0x2AF7398005AAA5C7,
        0x44DB015024623547,
    ]


def test_a_key_that_lost_precision_gives_an_unrelated_keystream():
    """The whole reason decode_key is carried as a string. The double holds
    13835058055282163712 exactly - it is 3 x 2^62 - so nothing goes wrong until JavaScript
    prints it back, which it does as the shortest decimal that round-trips:
    13835058055282164000. That string seeds a keystream with nothing in common with the
    right one, and no error is raised anywhere along the way.

    The digest is `decrypt.js`'s own output for the damaged key, recorded so this stays a
    check against the reference implementation rather than against ourselves.
    """
    damaged = keystream("13835058055282164000")
    assert damaged[:8] != keystream("13835058055282163712")[:8]
    assert (
        hashlib.sha256(damaged).hexdigest()
        == "3b137d5456a342a010edf0e1a113652f0cf63165afea4dcff8fab3ff935986ea"
    )


def test_length_is_honoured_and_is_a_prefix_of_the_full_stream():
    assert keystream("1", 100) == keystream("1")[:100]
    assert keystream("1", 0) == b""


def test_neighbouring_seeds_share_nothing():
    assert keystream("1234567890")[:64] != keystream("1234567891")[:64]


@pytest.mark.parametrize(
    "value",
    [
        "1.3835058055282164e+19",  # already through a float
        "13835058055282164000.0",
        "0x1f",
        "",
        "  ",
        "-1",
        "18446744073709551616",  # one past the uint64 ceiling
        "184467440737095516150",
    ],
)
def test_rejects_a_key_that_is_not_a_uint64_decimal_string(value):
    with pytest.raises(KeystreamError):
        keystream(value)


def test_rejects_a_key_that_is_not_a_string_at_all():
    with pytest.raises(KeystreamError):
        parse_decode_key(13835058055282163712)  # type: ignore[arg-type]


def test_accepts_the_uint64_ceiling_and_surrounding_whitespace():
    assert parse_decode_key(" 18446744073709551615 ") == 0xFFFFFFFFFFFFFFFF
    assert parse_decode_key("0") == 0


def test_rejects_a_negative_length():
    with pytest.raises(KeystreamError):
        keystream("1", -1)
