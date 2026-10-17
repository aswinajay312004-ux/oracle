# Spreadsheet format fixtures

Synthetic test files only — no client data.

| File | Made by | Purpose |
|------|---------|---------|
| `calamine_issues.xlsx/.xls/.xlsb/.ods` | The [calamine](https://github.com/tafia/calamine) project's own test workbook `tests/issues.*` (MIT licence, © 2016 Johann Tuffe) | The same content saved by real Excel/LibreOffice in four formats: our readers must return identical values for each |
| `merch_bom_libreoffice.ods/.html/.xls` | LibreOffice `soffice --convert-to` from `tests/fixtures.py:merch_bom()` | An independent program writes our synthetic merch BOM in other formats; parsing must find the same order |
| `layout_libreoffice.xlsx/.xls/.ods` | LibreOffice 24.2 `soffice --headless --convert-to xlsx/xls/ods` from a synthetic openpyxl workbook (sheet `PO`: merged A1:C1 and B6:B7, codes 8 formatted `0000`, 123 formatted `000-000`, 8 formatted `"ST-"0000`, hidden row 3, hidden column E, 1500 formatted `#,##0`; hidden sheet `Secret`) | Merged cells, hidden rows/columns/sheets and leading-zero number formats as another program stores them in three formats |
| `encrypted_msoffcrypto.xlsx` | [msoffcrypto-tool](https://github.com/nolze/msoffcrypto-tool) 6.0.0 (MIT) `OOXMLFile.encrypt('secret', …)` of a two-cell synthetic workbook; the tool was used only to make this file and is not a dependency | A real password-protected (MS-OFFCRYPTO agile encryption) workbook: an OLE2 file with `EncryptionInfo` + `EncryptedPackage` must be refused with the "password-protected" message |
| `word_libreoffice.doc` | LibreOffice 24.2 `soffice --headless --convert-to "doc:MS Word 97"` from a one-line text file | A real Word 97 binary (OLE2 with `WordDocument`) uploaded by mistake must get the "Word document" message, not an xlrd error |

Files that no installed program writes are built inside `tests/test_formats.py` instead: OLE2 containers for
IRM-protected workbooks, PowerPoint and Outlook files (`cfb()`), a raw BIFF2 worksheet (`biff2()`), Strict
Open XML (namespace rewrite of `layout_libreoffice.xlsx`), Excel-style MHTML (`mhtml()`), zips that lie about
their sizes (`lie_about_size()`), SpreadsheetML with styles and spans, and hostile ODS repeats.
