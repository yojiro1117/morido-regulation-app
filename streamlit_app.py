import streamlit as st
import pandas as pd
import io
import re
import time

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import ezdxf
except ImportError:
    ezdxf = None

from fpdf import FPDF

# guidelines dictionary for area and height thresholds
GUIDELINES = {
    "default": {
        "area_threshold": 500,  # 500 square meters
        "height_threshold": 2   # 2 meters
    }
}

def extract_info_from_pdf(uploaded_file):
    geoname, area, height = None, None, None
    if pdfplumber is None:
        return geoname, area, height
    # pdfplumber expects a file-like object with 'read' method; Streamlit uploads provide this
    with pdfplumber.open(uploaded_file) as pdf:
        text = ""
        for page in pdf.pages:
            try:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            except Exception:
                pass
    # extract geoname
    for prefix in ["地名：", "所在地：", "対象地：", "地名:", "所在地:", "対象地:"]:
        match = re.search(prefix + r"\s*([^\s]+)", text)
        if match:
            geoname = match.group(1).strip()
            break
    # extract area and height numbers
    area_match = re.search(r"(?:面積[:：]?)\s*([\d\.]+)", text)
    if area_match:
        try:
            area = float(area_match.group(1))
        except Exception:
            area = None
    height_match = re.search(r"(?:高さ[:：]?)\s*([\d\.]+)", text)
    if height_match:
        try:
            height = float(height_match.group(1))
        except Exception:
            height = None
    return geoname, area, height

def polygon_area(vertices):
    # compute area of polygon given list of vertices (x,y)
    if len(vertices) < 3:
        return 0.0
    area = 0.0
    for i in range(len(vertices)):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % len(vertices)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0

def extract_info_from_dxf(uploaded_file):
    geoname, area, height = None, None, None
    if ezdxf is None:
        return geoname, area, height
    try:
        # uploaded_file is file-like; need to read into bytes and load from memory
        data = uploaded_file.read()
        doc = ezdxf.readzip(io.BytesIO(data))
    except Exception:
        try:
            uploaded_file.seek(0)
            doc = ezdxf.readfile(uploaded_file)
        except Exception:
            return geoname, area, height
    msp = doc.modelspace()
    # compute area from closed polylines
    largest_area = 0.0
    for entity in msp:
        try:
            if entity.dxftype() in ["LWPOLYLINE", "POLYLINE"]:
                if hasattr(entity, "is_closed") and not entity.is_closed:
                    continue
                pts = [(v[0], v[1]) for v in entity.get_points()]
                a = polygon_area(pts)
                if a > largest_area:
                    largest_area = a
        except Exception:
            continue
        # extract height from text or mtext
        if entity.dxftype() in ["TEXT", "MTEXT"]:
            text = entity.text if entity.dxftype() == "TEXT" else entity.plain_text()
            match = re.search(r"(?:H=|高さ[:：]?)(\d+(?:\.\d+)?)", text)
            if match:
                try:
                    height = float(match.group(1))
                except Exception:
                    pass
    # convert units to square meters; assume units are in meters; if not, area conversion will be approximate
    area = largest_area if largest_area > 0 else None
    return geoname, area, height

def evaluate_plan(geoname, area, height):
    # returns dict with keys: permit_required(bool), improvements(list), category(str)
    thresholds = GUIDELINES["default"]
    result = {
        "permit_required": False,
        "improvements": [],
        "category": "許可不要"
    }
    if area is None or height is None:
        # insufficient info
        result["category"] = "情報不足"
        return result
    # Determine category
    requires_permit = area > thresholds["area_threshold"] or height > thresholds["height_threshold"]
    if requires_permit:
        result["permit_required"] = True
        result["category"] = "許可要"
        # Suggest improvements to avoid permit
        if area > thresholds["area_threshold"]:
            result["improvements"].append(f"面積を {thresholds['area_threshold']}㎡ 未満に縮小してください")
        if height > thresholds["height_threshold"]:
            result["improvements"].append(f"高さを {thresholds['height_threshold']}m 未満に抑えてください")
    else:
        # Determine if only notification (届出) is needed - simple example
        result["category"] = "届出要" if area > thresholds["area_threshold"] * 0.8 else "許可不要"
    return result

def generate_reports(results):
    # results: list of dicts
    # Generate Excel
    df = pd.DataFrame(results)
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    excel_data = excel_buffer.getvalue()
    # Generate PDF using fpdf
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, "盛土規制法 判定レポート\n")
    for row in results:
        pdf.multi_cell(0, 8, f"ファイル: {row['file']}")
        pdf.multi_cell(0, 8, f"地名: {row['地名'] or '不明'}")
        pdf.multi_cell(0, 8, f"面積: {row['面積'] or '不明'}㎡")
        pdf.multi_cell(0, 8, f"高さ: {row['高さ'] or '不明'}m")
        pdf.multi_cell(0, 8, f"判定区分: {row['申請区分']}")
        if row["不足情報"]:
            pdf.multi_cell(0, 8, f"不足情報: {row['不足情報']}")
        if row["改善案"]:
            pdf.multi_cell(0, 8, f"改善案: {row['改善案']}")
        pdf.ln(4)
    pdf_buffer = pdf.output(dest="S").encode("latin-1")
    return excel_data, pdf_buffer

st.set_page_config(page_title="盛土規制法 判定ツール（オンライン版）")
st.title("盛土規制法 判定ツール（オンライン版）")
st.write("PDFまたはDXFファイルをアップロードすると、盛土規制法に基づく申請要否を自動で判定し、改善案を提示します。")

uploaded_files = st.file_uploader(
    "ファイルをアップロードしてください（複数可）",
    type=["pdf", "dxf"],
    accept_multiple_files=True
)

if uploaded_files:
    total = len(uploaded_files)
    progress_bar = st.progress(0)
    status_area = st.empty()
    results = []
    for idx, uploaded_file in enumerate(uploaded_files, start=1):
        filename = uploaded_file.name
        status_area.info(f"{filename} を処理中…")
        # Determine file type
        ext = filename.split(".")[-1].lower()
        geoname, area, height = None, None, None
        if ext == "pdf":
            geoname, area, height = extract_info_from_pdf(uploaded_file)
        elif ext == "dxf":
            geoname, area, height = extract_info_from_dxf(uploaded_file)
        eval_res = evaluate_plan(geoname, area, height)
        result_row = {
            "file": filename,
            "地名": geoname,
            "面積": area,
            "高さ": height,
            "申請区分": eval_res["category"],
            "改善案": "；".join(eval_res["improvements"]) if eval_res["improvements"] else None,
            "不足情報": None if eval_res["category"] != "情報不足" else "地名や面積・高さの情報が不足しています"
        }
        results.append(result_row)
        # Update progress bar and estimated time
        progress = idx / total
        progress_bar.progress(progress)
        remaining = total - idx
        status_area.info(f"残りファイル数: {remaining} 件")
    progress_bar.progress(1.0)
    status_area.success("処理が完了しました。レポートをダウンロードできます。")
    # Display results
    df_results = pd.DataFrame(results)
    st.dataframe(df_results)
    # Generate reports
    excel_data, pdf_data = generate_reports(results)
    st.download_button("Excelレポートをダウンロード", excel_data, file_name="result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("PDFレポートをダウンロード", pdf_data, file_name="result.pdf", mime="application/pdf")
