"""Offline platform-error diagnostics, without credentials or live SDK calls."""

import json
import unittest
from unittest.mock import Mock

from wecom_archive import download as archive


class ChatDataErrorTests(unittest.TestCase):
    def message(self, detail, code=301042):
        return archive._chat_data_error_message({"errcode": code, "errmsg": detail})

    def test_ipv4_allowlist_error_keeps_only_validated_address(self):
        detail = (
            "get chatdata whiteip not match, hint:[PRIVATE_HINT], "
            "from ip:203.0.113.7, more info at https://example.invalid/?secret=PRIVATE_SECRET"
        )
        self.assertEqual(
            self.message(detail),
            "拉取對話失敗（平台錯誤碼 301042）；請求 IP 未通過會話存檔的可信 IP 白名單檢查"
            "；平台回報的請求 IP：203.0.113.7",
        )

    def test_ipv6_is_validated_and_canonicalized(self):
        self.assertTrue(self.message("from ip:2001:0DB8:0:0::7, hint:PRIVATE").endswith("2001:db8::7"))

    def test_bracketed_ipv6_is_supported(self):
        self.assertTrue(self.message("from ip:[2001:db8::7]; hint:PRIVATE").endswith("2001:db8::7"))

    def test_invalid_or_ambiguous_address_tokens_are_not_echoed(self):
        for candidate in (
            "999.0.0.1", "203.0.113.7:443", "[2001:db8::7]:443", "fe80::1%PRIVATE_ZONE",
            "203.0.113.7PRIVATE_SECRET", "https://example.invalid/PRIVATE_SECRET", "[[2001:db8::7]]",
        ):
            with self.subTest(candidate=candidate):
                text = self.message(f"get chatdata whiteip not match, from ip:{candidate}, hint:PRIVATE_HINT")
                self.assertNotIn("平台回報的請求 IP", text)
                self.assertNotIn(candidate, text)
                self.assertNotIn("PRIVATE", text)

    def test_absent_or_non_string_detail_keeps_fixed_reason(self):
        for detail in (None, 12, [], {}, "PRIVATE_SECRET without an address"):
            with self.subTest(detail=detail):
                text = self.message(detail)
                self.assertIn("可信 IP 白名單", text)
                self.assertNotIn("平台回報的請求 IP", text)
                self.assertNotIn("PRIVATE", text)

    def test_non_allowlist_platform_codes_stay_code_only(self):
        self.assertEqual(
            self.message("from ip:203.0.113.7, hint:PRIVATE_HINT secret:PRIVATE_SECRET", code=301052),
            "拉取對話失敗（平台錯誤碼 301052）",
        )

    def sdk(self, response, sdk_result=0):
        sdk = object.__new__(archive.FinanceSdk)
        sdk.sdk = 123
        sdk.lib = Mock()
        sdk.lib.NewSlice.return_value = 456
        sdk.lib.GetChatData.return_value = sdk_result
        sdk._slice_bytes = Mock(return_value=json.dumps(response).encode())
        return sdk

    def test_platform_error_is_used_at_sdk_boundary_and_slice_is_freed(self):
        sdk = self.sdk({"errcode": 301042, "errmsg": "from ip:203.0.113.7, hint:PRIVATE_HINT"})
        with self.assertRaises(archive.ArchiveError) as result:
            sdk.get_chat_data(2253, 100)
        self.assertIn("203.0.113.7", str(result.exception))
        self.assertNotIn("PRIVATE_HINT", str(result.exception))
        sdk.lib.GetChatData.assert_called_once_with(123, 2253, 100, None, None, 15, 456)
        sdk.lib.FreeSlice.assert_called_once_with(456)

    def test_success_returns_original_records_and_frees_slice(self):
        records = [{"seq": 2254, "encrypt_chat_msg": "preserved"}]
        sdk = self.sdk({"errcode": 0, "chatdata": records})
        self.assertEqual(sdk.get_chat_data(2253, 100), records)
        sdk.lib.FreeSlice.assert_called_once_with(456)

    def test_sdk_error_remains_code_only_and_frees_slice(self):
        sdk = self.sdk({}, sdk_result=10001)
        with self.assertRaisesRegex(archive.ArchiveError, "SDK 錯誤碼 10001"):
            sdk.get_chat_data(2253, 100)
        sdk._slice_bytes.assert_not_called()
        sdk.lib.FreeSlice.assert_called_once_with(456)

    def test_invalid_response_still_frees_slice(self):
        sdk = self.sdk({})
        sdk._slice_bytes.return_value = b"invalid json"
        with self.assertRaises(ValueError):
            sdk.get_chat_data(2253, 100)
        sdk.lib.FreeSlice.assert_called_once_with(456)


if __name__ == "__main__":
    unittest.main()
