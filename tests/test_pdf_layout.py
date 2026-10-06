from pathlib import Path

import matplotlib
import pymupdf
import pytest

from bttn.rendering import validate_pdf_layout


@pytest.mark.parametrize('count', [1, 2, 4])
def test_pdf_page_overflow_is_rejected(tmp_path, count):
    path = tmp_path / 'bad.pdf'
    with pymupdf.open() as pdf:
        for _ in range(count):
            pdf.new_page()
        pdf.save(path)
    with pytest.raises(ValueError, match='Expected 3 PDF pages'):
        validate_pdf_layout(path)


def test_pdf_substituted_font_blocks_publication(tmp_path):
    path = tmp_path / 'substituted.pdf'
    with pymupdf.open() as pdf:
        for heading in ['Tỷ giá USD-VND của NHNN', 'VNIBOR và SOFR', 'Bảng giá hàng hóa']:
            page = pdf.new_page()
            # Use a Unicode font so the heading gate passes before the font gate.
            font_file = Path(matplotlib.get_data_path()) / 'fonts/ttf/DejaVuSans.ttf'
            page.insert_font(fontname='WrongFont', fontfile=str(font_file))
            page.insert_textbox(pymupdf.Rect(20, 20, 560, 600), heading + '\n' + 'sample ' * 30,
                                fontname='WrongFont', fontsize=11)
        pdf.save(path)
    with pytest.raises(ValueError, match='substituted Times New Roman'):
        validate_pdf_layout(path)
