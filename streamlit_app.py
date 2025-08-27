import streamlit as st
import pandas as pd
import io
import re
import time
from datetime import datetime
try:
    import pdfplumber
except ImportError:
    pdfplumber = None
try:
    import ezdxf
except ImportError:
    ezdxf = None
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# Guidelines dictionary to hold threshold and references for prefecture and cities
guidelines = {
    "Fukuoka Prefecture": {
        "area_threshold": 500,
        "height_threshold": 2,
        "permit_required_text": "宿地造成及び特定盛土等規制法等の手引き 第5条第1項",
        "no_permit_text": "宿地造成及び特定盛土等規制法等の手引き 第4条",
        "page_line_info": {
            "permit": {"page": 12, "line": 5},
            "no_permit": {"page": 10, "line": 3}
        },
        "procedure": "福岡県県土整備部 建筑指导課のホームページ参照。申請書類、計画図面を提出し、審査期間约2週間。"
    },
    "大牧田市": {
        "area_threshold": 500,
        "height_threshold": 2,
        "permit_required_text": "大牧田市盛土規制に関する手引き 第3章",
        "no_permit_text": "大牧田市盛土規制に関する手引き 第2章",
        "page_line_info": {
            "permit": {"page": 8, "line": 10},
            "no_permit": {"page": 5, "line": 4}
        },
        "procedure": "大牧田市 建筑指导課で受付。申請構造計算書等を添付し、審査期間は纨3週間。"
    }
}

def extract_info_from_pdf(file):
    geoname = None
    area = None
    height = None
    if pdfplumber is None:
        return geoname, area, height
    with pdfplumber.open(file) as pdf:
        text = ""
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        # Extract geoname
        for key in ["地名", "所在地", "対象地"]:
            match = re.search(f"{key}[:：]\s*([^\n]+)", text)
            if match:
                geoname = match.group(1).strip()
                break
        # Extract area (numbers followed by m2 or ㎡)
        area_match = re.search(r"([\d,.]+)\s*(?:㎡|m2|m²)", text)
        if area_match:
            try:
                area = float(area_match.group(1).replace(',', ''))
            except ValueError:
                area = None
        # Extract height (numbers followed by m)
        height_match = re.search(r"高さ[:：]?\s*([\d,.]+)\s*m", text)
        if height_match:
            try:
                height = float(height_match.group(1).replace(',', ''))
            except ValueError:
                height = None
    return geoname, area, height

def extract_info_from_dxf(file):
    geoname = None
    area = None
    height = None
    if ezdxf is None:
        return geoname, area, height
    try:
        dxf = ezdxf.readfile(file)
        msp = dxf.modelspace()
        total_area = 0
        for entity in msp:
            if entity.dxftype() in ("HATCH", "LWPOLYLINE", "POLYLINE"):
                try:
                    if entity.dxftype() == "HATCH":
                        for path in entity.paths:
                            total_area += path.polygon.area
                    else:
                        if entity.is_closed:
                            # approximate area for polyline using geometry
                            pts = []
                            for v in entity.vertices:
                                pts.append((v.x, v.y))
                            if len(pts) > 2:
                                a = 0
                                for i in range(len(pts)):
                                    x1, y1 = pts[i]
                                    x2, y2 = pts[(i+1) % len(pts)]
                                    a += x1*y2 - x2*y1
                                total_area += abs(a) / 2
                except Exception:
                    pass
        if total_area > 0:
            area = abs(total_area)
    except Exception:
        pass
    return geoname, area, height

def evaluate_file(file_name, geoname, area, height):
    # Determine which guideline to use based on geoname; default to Fukuoka Prefecture
    guideline_key = "Fukuoka Prefecture"
    if geoname:
        if "大牟田" in geoname or "大牟田市" in geoname:
            guideline_key = "大牟田市"
    guide = guidelines[guideline_key]
    status = ""
    improvements = None
    reasons = []
    missing_info = []
    procedure = ""
    if not geoname:
        missing_info.append("地名")
    if area is None:
        missing_info.append("面積")
    if height is None:
        missing_info.append("高さ")
    if missing_info:
        status = "情報不足"
        improvements = "不足情報を入力して再評価してください。"
        reasons.append("地名や面積・高さの情報が不足しています")
        procedure = ""
    else:
        # Evaluate
        if area >= guide["area_threshold"] or height >= guide["height_threshold"]:
            status = "許可申請"
            reasons.append(f"{guideline_key}の規定で、面積{guide['area_threshold']}㎡以上または高さ{guide['height_threshold']}m以上は許可が必要")
            refs = guide["page_line_info"]["permit"]
            reasons.append(f"{guideline_key}資料 {refs['page']}ページ{refs['line']}行: {guide['permit_required_text']}")
            improvement_list = []
            if area >= guide["area_threshold"]:
                improvement_list.append(f"造成面積を{guide['area_threshold']}㎡未満に縮小する")
            if height >= guide["height_threshold"]:
                improvement_list.append(f"盛土の高さを{guide['height_threshold']}m未満に抑える")
            improvements = "、".join(improvement_list) if improvement_list else None
            procedure = guide["procedure"]
        else:
            status = "許可不要"
            reasons.append(f"{guideline_key}の規定で、面積{guide['area_threshold']}㎡未満かつ高さ{guide['height_threshold']}m未満の場合は許可不要")
            refs = guide["page_line_info"]["no_permit"]
            reasons.append(f"{guideline_key}資料 {refs['page']}ページ{refs['line']}行: {guide['no_permit_text']}")
            improvements = "現計画のまま許可は不要です"
            procedure = ""
    return {
        "file": file_name,
        "地名": geoname,
        "面積": area,
        "高さ": height,
        "申請区分": status,
        "改善案": improvements,
        "理由": " / ".join(reasons),
        "不足情報": "、".join(missing_info) if missing_info else None,
        "手続き": procedure
    }

def generate_reports(results):
    df = pd.DataFrame(results)
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="判定結果")
    excel_buffer.seek(0)
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    elements = []
    elements.append(Paragraph("盛土規制法 判定レポート", styles["Title"]))
    elements.append(Spacer(1, 12))
    table_data = [list(df.columns)]
    for _, row in df.iterrows():
        table_data.append([str(row[col]) if pd.notnull(row[col]) else "" for col in df.columns])
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0), colors.lightblue),
        ('TEXTCOLOR',(0,0),(-1,0), colors.whitesmoke),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('FONTNAME', (0,0),(-1,-1), "HeiseiMin-W3"),
        ('FONTSIZE', (0,0),(-1,-1), 8),
        ('BOX',(0,0),(-1,-1), 0.25, colors.black),
        ('GRID',(0,0),(-1,-1), 0.25, colors.black),
    ]))
    elements.append(table)
    doc.build(elements)
    pdf_buffer.seek(0)
    return excel_buffer.read(), pdf_buffer.read()

# Streamlit application
def main():
    st.title("盛土規制法 判定ツール（オンライン版）")
    st.write("PDFまたはDXFファイルをアップロードすると、盛土規制法に基づく申請要否を自動判定し、改善案を提示します。")
    uploaded_files = st.file_uploader("ファイルをアップロードしてください（複数可）", type=["pdf","dxf","dwg","jww"], accept_multiple_files=True)
    if uploaded_files:
        results = []
        progress_bar = st.progress(0)
        total = len(uploaded_files)
        for idx, uploaded_file in enumerate(uploaded_files):
            file_name = uploaded_file.name
            geoname = None
            area = None
            height = None
            if file_name.lower().endswith(".pdf"):
                geoname, area, height = extract_info_from_pdf(uploaded_file)
            elif any(file_name.lower().endswith(ext) for ext in [".dxf", ".dwg", ".jww"]):
                geoname, area, height = extract_info_from_dxf(uploaded_file)
            with st.expander(f"{file_name} の情報を入力／確認"):
                if not geoname:
                    geoname = st.text_input(f"{file_name} の地名を入力してください", key=f"geoname_{idx}") or geoname
                st.write(f"抽出された地名: {geoname}" if geoname else "地名は未取得です。入力してください。")
                if area is None or area == 0:
                    area = st.number_input(f"{file_name} の造成面積(㎡)を入力してください", min_value=0.0, value=0.0, step=0.1, key=f"area_{idx}") or None
                st.write(f"抽出された面積: {area}㎡" if area else "面積は未取得です。入力してください。")
                if height is None or height == 0:
                    height = st.number_input(f"{file_name} の高さ(m)を入力してください", min_value=0.0, value=0.0, step=0.1, key=f"height_{idx}") or None
                st.write(f"抽出された高さ: {height}m" if height else "高さは未取得です。入力してください。")
            result = evaluate_file(file_name, geoname, area if area else None, height if height else None)
            results.append(result)
            progress_bar.progress((idx + 1)/ total)
        df = pd.DataFrame(results)
        st.write("判定結果")
        st.dataframe(df)
        excel_data, pdf_data = generate_reports(results)
        st.download_button("Excelレポートをダウンロード", excel_data, file_name="morido_report.xlsx")
        st.download_button("PDFレポートをダウンロード", pdf_data, file_name="morido_report.pdf")
    else:
        st.info("ファイルをアップロードしてください。")

if __name__ == "__main__":
    main()
