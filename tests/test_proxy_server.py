import asyncio
import unittest

from Fleasion.proxy.server import (
    _build_modified_request,
    _read_body_wire,
    _read_headers_raw,
)


async def _read_message(data: bytes):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    headers = await _read_headers_raw(reader)
    body = await _read_body_wire(reader, headers.headers)
    return headers, body


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


if __name__ == "__main__":
    unittest.main()
