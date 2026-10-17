# Spreadsheet format fixtures

Synthetic test files only — no client data.

| File | Made by | Purpose |
|------|---------|---------|
| `calamine_issues.xlsx/.xls/.xlsb/.ods` | The [calamine](https://github.com/tafia/calamine) project's own test workbook `tests/issues.*` (MIT licence, © 2016 Johann Tuffe) | The same content saved by real Excel/LibreOffice in four formats: our readers must return identical values for each |
| `merch_bom_libreoffice.ods/.html/.xls` | LibreOffice `soffice --convert-to` from `tests/fixtures.py:merch_bom()` | An independent program writes our synthetic merch BOM in other formats; parsing must find the same order |
| `layout_libreoffice.xlsx/.xls/.ods` | LibreOffice 24.2 `soffice --headless --convert-to xlsx/xls/ods` from a synthetic openpyxl workbook (sheet `PO`: merged A1:C1 and B6:B7, codes 8 formatted `0000`, 123 formatted `000-000`, 8 formatted `"ST-"0000`, hidden row 3, hidden column E, 1500 formatted `#,##0`; hidden sheet `Secret`) | Merged cells, hidden rows/columns/sheets and leading-zero number formats as another program stores them in three formats |
| `encrypted_msoffcrypto.xlsx` | [msoffcrypto-tool](https://github.com/nolze/msoffcrypto-tool) 6.0.0 (MIT) `OOXMLFile.encrypt('secret', …)` of a two-cell synthetic workbook; the tool was used only to make this file and is not a dependency | A real password-protected (MS-OFFCRYPTO agile encryption) workbook: an OLE2 file with `EncryptionInfo` + `EncryptedPackage` must be refused with the "password-protected" message |
| `word_libreoffice.doc` | LibreOffice 24.2 `soffice --headless --convert-to "doc:MS Word 97"` from a one-line text file | A real Word 97 binary (OLE2 with `WordDocument`) uploaded by mistake must get the "Word document" message, not an xlrd error |
| `encrypted_libreoffice.xls/.ods` | LibreOffice 24.2 through its UNO API: a four-cell synthetic sheet saved with `storeToURL(..., Password='secret')` as "MS Excel 97" and "calc8" | Real password encryption written by another program: the `.xls` carries a BIFF `FILEPASS` record (xlrd must report it, not a monkeypatch), the `.ods` lists `manifest:encryption-data`; both must get the password message, not "damaged" |
| `merge_rows_libreoffice.xlsx` | LibreOffice 24.2 UNO: cell A1 "everything", then the whole sheet merged and saved as "Calc MS Excel 2007 XML" | LibreOffice writes the merge as the row-only reference `1:1048576`, which openpyxl and calamine both reject; a merged range covering the whole grid must be ignored with a note, not refuse the file or create 17 billion merged cells |
| `hidden_tail_libreoffice.xlsx/.xls/.ods` | LibreOffice 24.2 UNO: sheet `PO` with Style/Qty rows, row 4 (`OLD-STALE`, 999) hidden, rows 7 to the last row hidden and columns F to the last column hidden ("show only the form"), saved in three formats | Hidden rows holding stale data, and the `zeroHeight` / repeated-row way another program stores a hidden tail, must read the same in every format |
| `empty_first_libreoffice.xlsx/.xls/.ods` | LibreOffice 24.2 UNO: an empty first sheet `Cover`, then sheet `Order` with four cells | A workbook whose first sheet is empty: the data sheet must still be read, in every format |
| `sheets31_libreoffice.ods` | LibreOffice 24.2 UNO: 31 sheets, one cell each | More sheets than the 30-sheet limit, as calamine sees an `.ods` |

Files that no installed program writes are built inside `tests/test_formats.py` instead: OLE2 containers for
IRM-protected workbooks, PowerPoint and Outlook files (`cfb()`), a raw BIFF2 worksheet (`biff2()`), Strict
Open XML (namespace rewrite of `layout_libreoffice.xlsx`), Excel-style MHTML (`mhtml()`), zips that lie about
their sizes (`lie_about_size()`), SpreadsheetML with styles and spans, and hostile ODS repeats. The adversarial
cases (merged-range references, absurd cell and row counts, shared-string amplification, OLE2 directory chains,
decimal-comma and SAP report text, MHTML sheet names) are generated the same way in the tests that use them.
