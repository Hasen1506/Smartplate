import csv
import io

from smartplate.domain import receipts


def test_csv_untrusted_labels_cannot_be_spreadsheet_formulas(monkeypatch):
    labels = ['=HYPERLINK("https://example.invalid", "menu")', '+SUM(1,2)',
              '-1+2', '@SUM(1,2)', '  =1+1', '\tformula', '\rformula', '\nformula']
    rows = [dict(iso_date='2026-09-30', category='personal', amount=12.50, note=label)
            for label in labels]
    rows.append(dict(iso_date='2026-09-30', category='business', amount=27,
                     note='Dal, rice & vegetables'))
    monkeypatch.setattr(receipts, 'list_for', lambda uid: rows)
    exported = list(csv.reader(io.StringIO(receipts.export_csv(1))))
    for row, label in zip(exported[1:], labels):
        assert row[3] == "'" + label
        assert row[2] == '12.50'
    assert exported[-3] == ['2026-09-30', 'business', '27.00', 'Dal, rice & vegetables']
    assert exported[-1] == ['', 'business total', '27.00', '']
