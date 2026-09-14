from io import BytesIO
from zipfile import ZipFile

import pytest

from dispatch_fakes import image_data, product_rows
from dispatch_images import extract_sheet_images, match_image_pack
from dispatch_manager import catalog


def make_zip(files):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def workbook_image(anchor_row=0, anchor_col=12, no="no1", name="測試收納商品 1", external=False):
    rel_ns = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    sheet_ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return make_zip({
        "xl/workbook.xml": f'<workbook {sheet_ns}><sheets><sheet name="G正版" sheetId="1" r:id="r1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships {rel_ns}><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet {sheet_ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row></sheetData><drawing r:id="r2"/></worksheet>',
        "xl/sharedStrings.xml": f'<sst {sheet_ns}><si><t>{no}</t></si><si><t>{name}</t></si></sst>',
        "xl/worksheets/_rels/sheet1.xml.rels": f'<Relationships {rel_ns}><Relationship Id="r2" Target="../drawings/drawing1.xml"/></Relationships>',
        "xl/drawings/drawing1.xml": f'<d:wsDr xmlns:d="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><d:oneCellAnchor><d:from><d:col>{anchor_col}</d:col><d:row>{anchor_row}</d:row></d:from><d:pic><a:blip r:embed="r3"/></d:pic></d:oneCellAnchor></d:wsDr>',
        "xl/drawings/_rels/drawing1.xml.rels": f'<Relationships {rel_ns}><Relationship Id="r3" Target="../media/image1.png" {"TargetMode=\"External\"" if external else ""}/></Relationships>',
        "xl/media/image1.png": image_data(),
    })


def test_drawing_proposal_requires_matching_sheet_no_name_and_product_anchor():
    products = catalog(product_rows((1, 2)))
    result = extract_sheet_images(workbook_image(), products)
    assert len(result["images"]["G正版:no1"]) == 1
    assert not result["images"]["G正版:no2"]
    from dispatch_storage import asset_bytes
    assert asset_bytes(result["images"]["G正版:no1"][0]) == image_data()


@pytest.mark.parametrize("kwargs", [{"anchor_row": 6}, {"anchor_col": 11}, {"anchor_col": 20},
                                   {"no": "no999"}, {"name": "另一商品"}, {"external": True}])
def test_wrong_or_external_drawing_is_not_silently_paired(kwargs):
    result = extract_sheet_images(workbook_image(**kwargs), catalog(product_rows((1, 2))))
    assert not any(result["images"].values())


def test_zip_exact_filenames_pair_without_prefix_guessing_or_cross_vendor_ambiguity():
    products = catalog(product_rows((1, 2)))
    data = make_zip({"photos/TEST-1.png": image_data(), "BGD-G-2.png": image_data(),
                     "TEST-10.png": image_data(), "unrelated.jpg": image_data()})
    result = match_image_pack(data, products)
    assert all(len(images) == 1 for images in result["images"].values())
    assert len(result["warnings"]) == 2
    products[1]["supplier_code"] = "TEST-1"
    ambiguous = match_image_pack(make_zip({"TEST-1.png": image_data()}), products)
    assert not any(ambiguous["images"].values())


def test_corrupt_and_entity_archives_fail_closed():
    with pytest.raises(ValueError):
        match_image_pack(b"not-a-zip", catalog(product_rows((1,))))
    with pytest.raises(ValueError, match="外部實體"):
        extract_sheet_images(make_zip({"xl/workbook.xml": '<!DOCTYPE foo [<!ENTITY e "bad">]><foo/>'}), [])
    result = match_image_pack(make_zip({"TEST-1.png": b"not-a-picture"}), catalog(product_rows((1,))))
    assert not any(result["images"].values()) and result["warnings"]


def test_store_export_is_read_only_and_supplier_code_accepts_label_colon():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore
    from dispatch_fakes import FakeSpreadsheet
    rows = product_rows((1,))
    rows["G正版"][4][1] = "貨號： TEST-1"
    spreadsheet = FakeSpreadsheet(rows)
    spreadsheet.export = lambda format: workbook_image()
    store = CloudDispatchStore(spreadsheet)
    before = deepcopy(spreadsheet.sheets["G正版"].rows)
    products = store.catalog()
    assert products[0]["supplier_code"] == "TEST-1"
    assert store.source_images(products)["images"]["G正版:no1"]
    assert set(spreadsheet.sheets) == {"G正版"}
    assert spreadsheet.sheets["G正版"].rows == before
