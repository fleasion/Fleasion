from __future__ import annotations

import unittest

from fleasion.cache.tools.solidmodel_converter.rbxm.binary_writer import interleave_f32
from fleasion.cache.tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer
from fleasion.cache.tools.solidmodel_converter.rbxm.types import PropertyFormat


class RbxmCFrameOrientationTests(unittest.TestCase):
    def test_sparse_axis_aligned_orientation_ids_decode_to_roblox_matrices(self) -> None:
        expected = {
            0x02: (1, 0, 0, 0, 1, 0, 0, 0, 1),
            0x03: (1, 0, 0, 0, 0, -1, 0, 1, 0),
            0x05: (1, 0, 0, 0, -1, 0, 0, 0, -1),
            0x06: (1, 0, 0, 0, 0, 1, 0, -1, 0),
            0x07: (0, 1, 0, 1, 0, 0, 0, 0, -1),
            0x09: (0, 0, 1, 1, 0, 0, 0, 1, 0),
            0x0A: (0, -1, 0, 1, 0, 0, 0, 0, 1),
            0x0C: (0, 0, -1, 1, 0, 0, 0, -1, 0),
            0x0D: (0, 1, 0, 0, 0, 1, 1, 0, 0),
            0x0E: (0, 0, -1, 0, 1, 0, 1, 0, 0),
            0x10: (0, -1, 0, 0, 0, -1, 1, 0, 0),
            0x11: (0, 0, 1, 0, -1, 0, 1, 0, 0),
            0x14: (-1, 0, 0, 0, 1, 0, 0, 0, -1),
            0x15: (-1, 0, 0, 0, 0, 1, 0, 1, 0),
            0x17: (-1, 0, 0, 0, -1, 0, 0, 0, 1),
            0x18: (-1, 0, 0, 0, 0, -1, 0, -1, 0),
            0x19: (0, 1, 0, -1, 0, 0, 0, 0, 1),
            0x1B: (0, 0, -1, -1, 0, 0, 0, 1, 0),
            0x1C: (0, -1, 0, -1, 0, 0, 0, 0, -1),
            0x1E: (0, 0, 1, -1, 0, 0, 0, -1, 0),
            0x1F: (0, 1, 0, 0, 0, -1, -1, 0, 0),
            0x20: (0, 0, 1, 0, 1, 0, -1, 0, 0),
            0x22: (0, -1, 0, 0, 0, 1, -1, 0, 0),
            0x23: (0, 0, -1, 0, -1, 0, -1, 0, 0),
        }
        zero_positions = interleave_f32([0.0]) * 3

        for orientation_id, matrix in expected.items():
            with self.subTest(orientation_id=orientation_id):
                values, offset = RbxmDeserializer()._read_cframes_with_offset(  # pyright: ignore[reportPrivateUsage]
                    bytes([orientation_id]) + zero_positions,
                    0,
                    1,
                    PropertyFormat.CFRAME_MATRIX,
                )

                self.assertEqual(offset, 13)
                self.assertEqual(
                    tuple(values[0][f'R{row}{column}'] for row in range(3) for column in range(3)),
                    matrix,
                )


if __name__ == '__main__':
    unittest.main()
