# Spreadsheet format fixtures

Synthetic test files only — no client data.

| File | Made by | Purpose |
|------|---------|---------|
| `calamine_issues.xlsx/.xls/.xlsb/.ods` | The [calamine](https://github.com/tafia/calamine) project's own test workbook `tests/issues.*` (MIT licence, © 2016 Johann Tuffe) | The same content saved by real Excel/LibreOffice in four formats: our readers must return identical values for each |
| `merch_bom_libreoffice.ods/.html/.xls` | LibreOffice `soffice --convert-to` from `tests/fixtures.py:merch_bom()` | An independent program writes our synthetic merch BOM in other formats; parsing must find the same order |
