#!/usr/bin/env python3
"""Host-only regression tests for the source-matched nanohub IIO decoder."""
import importlib.util
from pathlib import Path
import struct
import unittest


SOURCE = Path(__file__).with_name("sensorhub-sample-once.py")
spec = importlib.util.spec_from_file_location("sensorhub_sample", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SensorHubDecodeTests(unittest.TestCase):
    def test_source_abi_frame_sizes(self):
        self.assertEqual(struct.calcsize(mod.SENSORS["accel"][2]), 14)
        self.assertEqual(struct.calcsize(mod.SENSORS["gyro"][2]), 20)
        self.assertEqual(struct.calcsize(mod.SENSORS["mag"][2]), 22)
        self.assertEqual(struct.calcsize(mod.SENSORS["light"][2]), 38)

    def test_sensor_bits_are_not_iio_numbers(self):
        self.assertEqual(mod.SENSORS['mag'][1], 4)
        self.assertEqual(mod.SENSORS['light'][1], 9)

    def test_magnetometer_preserves_status_bytes(self):
        values = (-10, 20, -30, 3, 0, 123456789)
        layout = mod.SENSORS['mag'][2]
        self.assertEqual(mod.decode(struct.pack(layout, *values), layout), [values])

    def test_light_has_unsigned_lux_signed_cct_and_no_padding(self):
        values = (4000000000, -123, 1, 2, 3, 4, 65535, 10, 255, 1, 987654321)
        layout = mod.SENSORS['light'][2]
        self.assertEqual(mod.decode(struct.pack(layout, *values), layout), [values])

    def test_every_payload_field_has_a_name(self):
        for sensor, (_, _, layout) in mod.SENSORS.items():
            row = struct.unpack(layout, bytes(struct.calcsize(layout)))
            self.assertEqual(len(row) - 1, len(mod.FIELDS[sensor]))

    def test_accel_is_little_endian_signed_s16_then_u64(self):
        payload = struct.pack("<hhhQ", -32768, 0, 32767, 0x0102030405060708)
        self.assertEqual(mod.decode(payload, "<hhhQ"),
                         [(-32768, 0, 32767, 0x0102030405060708)])

    def test_gyro_is_little_endian_signed_s32_then_u64(self):
        payload = struct.pack("<iiiQ", -1, 0x7FFFFFFF, -0x80000000, 99)
        self.assertEqual(mod.decode(payload, "<iiiQ"),
                         [(-1, 0x7FFFFFFF, -0x80000000, 99)])

    def test_partial_frame_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            mod.decode(b"\0" * 13, "<hhhQ")

    def test_multiple_frames_preserve_order(self):
        payload = struct.pack("<hhhQhhhQ", 1, 2, 3, 10, 4, 5, 6, 11)
        self.assertEqual(mod.decode(payload, "<hhhQ"),
                         [(1, 2, 3, 10), (4, 5, 6, 11)])


if __name__ == "__main__":
    unittest.main()
