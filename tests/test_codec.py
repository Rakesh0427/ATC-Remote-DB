import unittest
from pathlib import Path

from codec import BYTES_PER_SLICE, TEXT_MAX, CodecError, decode_vhf, encode_vhf, inspect_bin

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "SGC10001.bin"


class CodecTests(unittest.TestCase):
    def test_round_trip_sizes(self):
        for size in (1, BYTES_PER_SLICE, BYTES_PER_SLICE + 1, 200, 8204):
            image = bytes((i * 17 + 3) & 0xFF for i in range(size))
            encoded = encode_vhf(image)
            self.assertLessEqual(encoded.max_text, TEXT_MAX)
            self.assertEqual(decode_vhf(encoded.text), image)

    def test_full_slice_uses_the_whole_text_field(self):
        encoded = encode_vhf(b"\x11" * BYTES_PER_SLICE)
        texts = [line.split("<STX>", 1)[1].split("<ET", 1)[0] for line in encoded.text.splitlines() if "<STX>" in line]
        self.assertTrue(any(len(text) == TEXT_MAX for text in texts))

    def test_message_suffixes_and_block_ids(self):
        encoded = encode_vhf(b"\x22" * 8204, blocks_per_message=16)
        lines = [line for line in encoded.text.splitlines() if line.startswith("<SOH>")]
        self.assertEqual(len(lines), encoded.air_blocks)
        self.assertGreater(encoded.messages, 1)
        # First message: 16 blocks, ids A..P, ETB until the last.
        first = lines[:16]
        self.assertTrue(all("<ETB>" in line for line in first[:-1]))
        self.assertIn("<ETX>", first[-1])
        ids = [line.split("<STX>", 1)[0][-1] for line in first]
        self.assertEqual(ids, list("ABCDEFGHIJKLMNOP"))
        self.assertIn("<ETX>", lines[-1])

    def test_sample_database(self):
        image = SAMPLE.read_bytes()
        info = inspect_bin(image)
        self.assertTrue(info.recognized)
        self.assertEqual(info.part, "69001264-505")
        self.assertEqual(info.db_id, "B5678")
        self.assertEqual(len(info.centres), 83)
        self.assertEqual(info.slots, 148)
        edyy = next(c for c in info.centres if c.gfd == "EDYY")
        self.assertEqual(edyy.tsel, "cl")
        self.assertEqual(edyy.country, "eu")
        encoded = encode_vhf(image)
        self.assertEqual(encoded.data_slices, 88)
        self.assertEqual(encoded.air_blocks, 89)
        self.assertEqual(encoded.messages, 6)
        self.assertNotIn("RA", encoded.text.split("<STX>", 1)[0])
        self.assertIn("<ACK>C1A<STX>", encoded.text)
        self.assertEqual(decode_vhf(encoded.text), image)

    def test_crc_tamper_is_rejected(self):
        encoded = encode_vhf(b"\x33" * 100)
        broken = encoded.text.replace("ATDB/U/", "ATDB/X/", 1)
        with self.assertRaises(CodecError):
            decode_vhf(broken)

    def test_bad_label(self):
        with self.assertRaises(CodecError):
            encode_vhf(b"\x00", label="RAA")


if __name__ == "__main__":
    unittest.main()
