import asyncio
import tempfile
import unittest
from pathlib import Path

from Fleasion.proxy.server import (
    _build_modified_request,
    _is_empty_json_array,
    _read_body_wire,
    _read_headers_raw,
    _serve_local_file,
)


async def _read_message(data: bytes):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    headers = await _read_headers_raw(reader)
    body = await _read_body_wire(reader, headers.headers)
    return headers, body


def _response_body(response: bytes) -> bytes:
    return response.split(b"\r\n\r\n", 1)[1]


class ProxyServerRawHttpTests(unittest.TestCase):
    def test_raw_header_preservation_duplicate_headers_and_casing(self):
        data = (
            b"GET /asset HTTP/1.1\r\n"
            b"Host: assetdelivery.roblox.com\r\n"
            b"X-Dupe: one\r\n"
            b"x-dupe: two\r\n"
            b"\r\n"
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertEqual(headers.raw_header_block, data)
        self.assertEqual(headers.first_line, b"GET /asset HTTP/1.1")
        self.assertEqual(headers.headers[b"x-dupe"], b"two")
        self.assertEqual(headers.raw_header_block.count(b"X-Dupe"), 1)
        self.assertEqual(headers.raw_header_block.count(b"x-dupe"), 1)
        self.assertEqual(body.wire, b"")
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_bodyless_get_passthrough_does_not_inject_content_length(self):
        data = b"GET /v1/assets/batch HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n\r\n"

        headers, body = asyncio.run(_read_message(data))

        forwarded = headers.raw_header_block + body.wire
        self.assertEqual(forwarded, data)
        self.assertNotIn(b"content-length", forwarded.lower())

    def test_content_length_post_exact_passthrough(self):
        data = (
            b"POST /v1/assets/batch HTTP/1.1\r\n"
            b"Host: assetdelivery.roblox.com\r\n"
            b"Content-Length: 11\r\n"
            b"\r\n"
            b"hello world"
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertEqual(body.payload, b"hello world")
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_chunked_request_exact_wire_preservation(self):
        data = (
            b"POST /chunk HTTP/1.1\r\n"
            b"Host: assetdelivery.roblox.com\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5;ext=1\r\nhello\r\n"
            b"6\r\n world\r\n"
            b"0\r\nTrailer: value\r\n\r\n"
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertTrue(body.was_chunked)
        self.assertEqual(body.payload, b"hello world")
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_chunked_response_exact_wire_preservation(self):
        data = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"3\r\nabc\r\n"
            b"0\r\n\r\n"
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertTrue(body.was_chunked)
        self.assertEqual(body.payload, b"abc")
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_modified_request_strips_transfer_encoding_and_sets_content_length(self):
        request = _build_modified_request(
            b"POST /v1/assets/batch HTTP/1.1",
            {
                b"host": b"assetdelivery.roblox.com",
                b"transfer-encoding": b"chunked",
                b"content-length": b"999",
                b"content-encoding": b"gzip",
            },
            b"{}",
        )

        head = request.split(b"\r\n\r\n", 1)[0].lower()
        self.assertNotIn(b"transfer-encoding", head)
        self.assertNotIn(b"content-encoding", head)
        self.assertNotIn(b"content-length: 999", head)
        self.assertIn(b"content-length: 2", head)
        self.assertTrue(request.endswith(b"\r\n\r\n{}"))

    def test_empty_json_array_detection_for_filtered_batches(self):
        self.assertTrue(_is_empty_json_array(b" [] \r\n"))
        self.assertFalse(_is_empty_json_array(b'[{"assetId":1}]'))
        self.assertFalse(_is_empty_json_array(b""))

    def test_local_extensionless_roblox_file_strips_metadata_prefix(self):
        expected = b"<roblox version=\"4\"></roblox>"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset_hash"
            path.write_bytes(b"RBXH metadata bytes\r\n" + expected)

            response = _serve_local_file(str(path))

        self.assertIn(f"Content-Length: {len(expected)}".encode(), response)
        self.assertEqual(_response_body(response), expected)

    def test_local_bin_roblox_file_strips_metadata_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.bin"
            path.write_bytes(b"metadata\n\n<roblox><Item /></roblox>")

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), b"<roblox><Item /></roblox>")

    def test_local_non_target_extension_keeps_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.rbxmx"
            content = b"metadata\n<roblox><Item /></roblox>"
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)

    def test_local_target_extension_without_roblox_marker_keeps_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.bin"
            content = b"not a roblox document"
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)


if __name__ == "__main__":
    unittest.main()
