import streamlit as st
import pandas as pd
import io
import re

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import ezdxf
except ImportError:
    ezdxf = None


def extract_info_from_pdf(file):
    geoname, area, height = None, None, None
    if pdfplumber is None:
        return geoname, area, height
    text = ''
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
            except Exception:
                pass
    for prefix in ['地名：','所在地：','対象地：','地域名：']:
        match = re.search(prefix + r'\s*([^\n]+)', text)
        if match:
            geoname = match.group(1).strip()
            break
    # Height and area extraction (simple heuristic): look for numbers followed by m or m2
    # This example does not implement full parsing.
    return geoname, area, height


def extract_info_from_dxf(file):
    geoname, area, height = None, None, None
    if ezdxf is None:
        return geoname, area, height
    try:
        doc = ezdxf.read(file)
    except Exception:
        return geoname, area, height
    msp = doc.modelspace()
    area_val = 0.0
    # accumulate area from hatches and closed polylines
    for h in msp.query('HATCH'):
        try:
            area_val += abs(h.get_area())
        except Exception:
            pass
    for pl in msp.query('LWPOLYLINE'):
        try:
            if pl.closed:
                area_val += abs(pl.area)
        except Exception:
            pass
    if area_val > 0:
        area = area_val
    return geoname, area, height


def evaluate(area, height):
    area_threshold = 500.0
    height_threshold = 2.0
    suggestions = []
    if area is None or height is None:
        return '情報不十分', ['面積や高さ情報が不足しています。追加資料を提供してください。']
    need_permit = area > area_threshold or height > height_threshold
    if need_permit:
        status = '申請要'
        if area > area_threshold:
            suggestions.append(f'造成面積を{area_threshold}㎡未満に縮小')
        if height > height_threshold:
            suggestions.append(f'盛土高さを{height_threshold}m未満に抑える')
    else:
        # if there is some development but within limits, require notification
        status = '届出要' if area > 0 or height > 0 else '不要'
    return status, suggestions


def main():
    st.title('盛土規制法 判定ツール (オンライン版)')
    uploaded_files = st.file_uploader('PDFまたはDXFファイルをアップロード', accept_multiple_files=True, type=['pdf','dxf'])
    if uploaded_files:
        results = []
        progress = st.progress(0)
        for idx, uploaded_file in enumerate(uploaded_files):
            file_ext = uploaded_file.name.split('.')[-1].lower()
            geoname, area, height = None, None, None
            if file_ext == 'pdf':
                geoname, area, height = extract_info_from_pdf(uploaded_file)
            elif file_ext == 'dxf':
                geoname, area, height = extract_info_from_dxf(uploaded_file)
            status, suggestions = evaluate(area, height)
            results.append({
                'ファイル名': uploaded_file.name,
                '地名': geoname or '',
                '面積': area,
                '高さ': height,
                '判定': status,
                '提案': ';'.join(suggestions)
            })
            progress.progress((idx + 1) / len(uploaded_files))
        df = pd.DataFrame(results)
        st.write('判定結果')
        st.dataframe(df)
        # create Excel
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False)
        excel_buffer.seek(0)
        st.download_button('Excelレポートをダウンロード', excel_buffer, file_name='morido_report.xlsx')
        # CSV report as a simple alternative to PDF
        csv_content = df.to_csv(index=False)
        st.download_button('CSVレポートをダウンロード', csv_content, file_name='morido_report.csv')


if __name__ == '__main__':
    main()
