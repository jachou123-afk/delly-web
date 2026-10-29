"""Synthetic images and memory-only ZIPs; never NAS, browser or cloud writes."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from PIL import Image

from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes
import quote_image_repair_zip as repair_zip


def image(number=0):
    output = BytesIO()
    Image.new("RGB", (24, 18), (number % 255, 67, 98)).save(output, "PNG")
    return output.getvalue()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def manifest(count=1, products=None):
    return {"schema": 1, "products": products if products is not None else [
        {"sheet": "F方小姐", "actual_no": "no42", "sha256": [sha(image(n)) for n in range(count)]}]}


def pack(value=None, entries=None, compression=ZIP_DEFLATED, manifest_raw=None):
    output = BytesIO()
    if entries is None:
        entries = [("images/" + sha(image()) + ".png", image())]
    with ZipFile(output, "w", compression) as archive:
        if manifest_raw is not False:
            archive.writestr("manifest.json", manifest_raw if manifest_raw is not None else
                             json.dumps(value or manifest(), ensure_ascii=False).encode())
        for name, raw in entries:
            archive.writestr(name, raw)
    return output.getvalue()


@pytest.mark.parametrize("count", [1, 6, 10])
@pytest.mark.parametrize("compression", [ZIP_STORED, ZIP_DEFLATED])
def test_original_order_main_first_and_all_bytes_preserved(count, compression):
    raws = [image(n) for n in range(count)]
    value = manifest(count)
    value["products"][0]["sha256"].reverse()
    packed = pack(value, [("images/" + sha(raw) + ".png", raw) for raw in raws], compression)
    result = repair_zip.read_repair_zip(packed)
    assert result["schema"] == 1 and result["package_sha256"] == sha(packed)
    assert result["products"] == [{**value["products"][0], "identity": "F方小姐:no42"}]
    assert result["products"][0]["sha256"][0] == sha(raws[-1])
    assert {key: asset_bytes(asset) for key, asset in result["assets"].items()} == {sha(raw): raw for raw in raws}
    assert result["digest"] == repair_zip.package_digest(result)


def test_shared_sha_across_products_requires_only_one_physical_original():
    products = [{"sheet": "W玩具", "actual_no": "no1766", "sha256": [sha(image())]},
                {"sheet": "F方小姐", "actual_no": "no42", "sha256": [sha(image())]}]
    result = repair_zip.read_repair_zip(pack(manifest(products=products)))
    assert len(result["products"]) == 2 and len(result["assets"]) == 1
    assert [p["identity"] for p in result["products"]] == ["W玩具:no1766", "F方小姐:no42"]


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema=True), lambda m: m.update(schema="1"), lambda m: m.update(schema=1.0),
    lambda m: m.update(schema=2), lambda m: m.update(products=[]),
    lambda m: m.update(products={}), lambda m: m.update(price=17.5),
    lambda m: m["products"][0].update(price=1),
    lambda m: m["products"][0].update(sheet=""), lambda m: m["products"][0].update(sheet=" W玩具"),
    lambda m: m["products"][0].update(sheet="W:玩具"), lambda m: m["products"][0].update(sheet="W\x00玩具"),
    lambda m: m["products"][0].update(sheet="W\u202e玩具"), lambda m: m["products"][0].update(sheet="x" * 81),
    lambda m: m["products"][0].update(sheet=True),
    lambda m: m["products"][0].update(actual_no="no0"), lambda m: m["products"][0].update(actual_no="no042"),
    lambda m: m["products"][0].update(actual_no="42"), lambda m: m["products"][0].update(actual_no="NO42"),
    lambda m: m["products"][0].update(actual_no=42), lambda m: m["products"][0].update(actual_no="no42\n"),
    lambda m: m["products"][0].update(sha256=[]), lambda m: m["products"][0].update(sha256="0" * 64),
    lambda m: m["products"][0].update(sha256=["z" * 64]), lambda m: m["products"][0].update(sha256=["A" * 64]),
    lambda m: m["products"][0].update(sha256=[None]),
    lambda m: m["products"][0].update(sha256=[sha(image()), sha(image())]),
    lambda m: m["products"][0].update(sha256=[sha(image(n)) for n in range(11)]),
    lambda m: m["products"].append(deepcopy(m["products"][0])),
])
def test_manifest_types_extras_identity_and_count_rejected(mutate):
    value = manifest()
    mutate(value)
    with pytest.raises(DispatchError):
        repair_zip.read_repair_zip(pack(value))


@pytest.mark.parametrize("raw", [
    b'{"schema":1,"schema":1,"products":[]}',
    ('{"schema":1,"products":[{"sheet":"F方小姐","actual_no":"no42","sha256":[],"sha256":[]}]}').encode(),
    b'not json', b'\xff', b'null', b'[]', b'{"schema":1}',
])
def test_duplicate_json_keys_and_invalid_encoding_never_accepted(raw):
    with pytest.raises(DispatchError):
        repair_zip.read_repair_zip(pack(manifest_raw=raw))


@pytest.mark.parametrize("path", [
    "../private.png", "/private.png", "C:/private.png", "images\\private.png",
    "images/../private.png", "images//private.png", "./manifest.json", "Manifest.json",
    "private.txt", "chat.json", "images/", "images/" + "A" * 64 + ".png",
    "images/" + "0" * 64 + ".gif", "images/" + "0" * 64 + ".PNG",
])
def test_only_exact_relative_manifest_and_sha_image_paths_are_accepted(path):
    with pytest.raises(DispatchError) as error:
        repair_zip.read_repair_zip(pack(entries=[(path, b"private-content")]))
    assert "private" not in str(error.value)


def test_missing_unreferenced_duplicate_and_wrong_hash_originals_rejected():
    expected = sha(image())
    cases = [
        (pack(entries=[]), "缺少"),
        (pack(entries=[("images/" + sha(image(1)) + ".png", image(1))]), "未引用"),
        (pack(entries=[("images/" + expected + ".png", image()),
                       ("images/" + expected + ".jpg", image())]), "重複 SHA"),
        (pack(entries=[("images/" + expected + ".png", image(1))]), "SHA-256 不符"),
        (pack(entries=[("images/" + expected + ".jpg", image())]), "實際圖片格式"),
        (pack(manifest_raw=False), "manifest.json"),
    ]
    for packed, message in cases:
        with pytest.raises(DispatchError, match=message):
            repair_zip.read_repair_zip(packed)


def test_duplicate_filenames_and_symlinks_rejected_before_contents_are_read():
    filename = "images/" + sha(image()) + ".png"
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = pack(entries=[(filename, image()), (filename, image())])
    with pytest.raises(DispatchError, match="重複檔案"):
        repair_zip.read_repair_zip(duplicate)
    link = ZipInfo(filename)
    link.external_attr = 0o120777 << 16
    with pytest.raises(DispatchError, match="連結"):
        repair_zip.read_repair_zip(pack(entries=[(link, b"private-target")]))


@pytest.mark.parametrize("limit", ["MAX_ZIP_BYTES", "MAX_TOTAL_BYTES", "MAX_ENTRIES",
                                    "MAX_PRODUCTS", "MAX_MANIFEST_BYTES", "MAX_IMAGE_BYTES"])
def test_all_memory_and_file_limits_are_enforced(monkeypatch, limit):
    packed = pack()
    monkeypatch.setattr(repair_zip, limit, 0)
    with pytest.raises(DispatchError):
        repair_zip.read_repair_zip(packed)


def test_unsupported_compression_crc_and_non_zip_rejected():
    with pytest.raises(DispatchError, match="壓縮"):
        repair_zip.read_repair_zip(pack(compression=ZIP_BZIP2))
    with pytest.raises(DispatchError, match="損壞"):
        repair_zip.read_repair_zip(b"private not zip")
    packed = bytearray(pack())
    name_len = int.from_bytes(packed[26:28], "little")
    packed[30 + name_len + 4] ^= 0x20
    with pytest.raises(DispatchError):
        repair_zip.read_repair_zip(bytes(packed))


def test_encrypted_member_and_nul_alias_rejected():
    packed = bytearray(pack())
    # Set encryption in local and central flags for the manifest member.
    local = packed.find(b"PK\x03\x04")
    central = packed.find(b"PK\x01\x02")
    packed[local + 6] |= 1
    packed[central + 8] |= 1
    with pytest.raises(DispatchError, match="加密"):
        repair_zip.read_repair_zip(bytes(packed))
    packed = pack().replace(b"manifest.json", b"manifest\x00json")
    with pytest.raises(DispatchError, match="路徑"):
        repair_zip.read_repair_zip(packed)


def test_declared_and_actual_size_must_match_with_bounded_read():
    limits = []
    class Member(BytesIO):
        def read(self, size=-1):
            limits.append(size)
            return super().read(size)
    archive = SimpleNamespace(open=lambda entry: Member(b"123"))
    with pytest.raises(DispatchError, match="大小不符"):
        repair_zip._read_member(archive, SimpleNamespace(file_size=4), 5)
    assert limits == [6]


@pytest.mark.parametrize("mutate", [
    lambda p: p["products"][0].update(identity="W玩具:no1"),
    lambda p: p["products"][0].update(price=1),
    lambda p: p["assets"].clear(),
    lambda p: next(iter(p["assets"].values())).update(data="invalid"),
    lambda p: next(iter(p["assets"].values())).update(mime=[]),
    lambda p: next(iter(p["assets"].values())).update(name="private.png"),
])
def test_package_digest_rechecks_normalized_contents_including_original_bytes(mutate):
    package = repair_zip.read_repair_zip(pack())
    mutate(package)
    with pytest.raises(DispatchError):
        repair_zip.package_digest(package)


def test_product_order_and_main_image_order_change_package_digest():
    raws = [image(0), image(1)]
    products = [{"sheet": "W玩具", "actual_no": "no1", "sha256": [sha(raw) for raw in raws]},
                {"sheet": "W玩具", "actual_no": "no2", "sha256": [sha(raws[0])]}]
    package = repair_zip.read_repair_zip(pack(manifest(products=products),
        [("images/" + sha(raw) + ".png", raw) for raw in raws]))
    original_digest = package["digest"]
    package["products"][0]["sha256"].reverse()
    assert repair_zip.package_digest(package) != original_digest
    package["products"][0]["sha256"].reverse()
    package["products"].reverse()
    assert repair_zip.package_digest(package) != original_digest
