import pytest
from scripts.audit_schema_reference import audit, parse_catalog


CATALOG = '''TABLE 1 of 2: HEADER   (rows ~1)
  Columns (1):
    HEADERID                        NUMBER(*,0)      NOT NULL
  Links (1):
    DETAIL.HEADERID  <->  HEADER.HEADERID    [client report join]
TABLE 2 of 2: DETAIL   (rows ~2)
  Columns (2):
    DETAILID                        NUMBER(*,0)      NOT NULL
    HEADERID                        NUMBER(*,0)
  Links (1):
    DETAIL.HEADERID  <->  HEADER.HEADERID    [client report join]
'''


def test_counts_columns_and_deduplicates_mirrored_links():
    tables, links = parse_catalog(CATALOG)
    assert tables == {'HEADER': {'HEADERID'}, 'DETAIL': {'DETAILID', 'HEADERID'}}
    assert len(links) == 1


def test_table_without_row_statistics_is_still_parsed():
    assert parse_catalog(CATALOG.replace('   (rows ~2)', '')) == parse_catalog(CATALOG)


def test_header_disagreement_is_explicit():
    result = audit('Tables: 2   Columns: 5\n' + CATALOG, [], {'tables': {}, 'masters': {}})
    assert result['reference_columns'] == 3 and result['header_columns'] == 5
    assert result['warnings']


def test_mapping_audit_reports_gaps_without_promoting_evidence():
    result = audit(CATALOG, [{'table': 'HEADER', 'column': 'HEADERID'}],
                   {'tables': {'DETAIL': {'columns': {'HEADERID': {}, 'MISSING': {}}}},
                    'masters': {'buyer': {'table': 'BUYER', 'key': 'ID', 'label': 'NAME'}}})
    assert result['mapped_columns_missing_in_reference'] == ['DETAIL.MISSING']
    assert result['mapped_masters_absent_locally'] == ['BUYER']
    assert result['master_columns_missing_in_reference'] == ['BUYER.ID', 'BUYER.NAME']


@pytest.mark.parametrize('text', [CATALOG.split('TABLE 2')[0], CATALOG.replace('Columns (2)', 'Columns (3)'),
                                CATALOG.replace('TABLE 2 of 2: DETAIL', 'TABLE 2 of 2: HEADER')])
def test_partial_or_corrupted_catalog_is_rejected(text):
    with pytest.raises(ValueError):
        parse_catalog(text)
