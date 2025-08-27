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

# ReportLab imports
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# Register Japanese font
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3'))

# Guidelines dictionary
guidelines = {
    "Fukuoka Prefecture": {
        "area_threshold": 500,
        "height_threshold": 2,
        "permit_required_text": "許可が必要：盛土の高さ2m以上または面積500㎡以上の場合は許可申請が必要です。",
        "no_permit_text": "許可不要：高さ2m未満かつ面積500㎡未満の場合は許可不要です。",
        "page_line_info": "手引きP10 L5-L10",
        "procedure": "窓口：福岡県、提出書類：盛土許可申請書、審査日数：約30日、手数料：無料"
    },
    "大牟田市": {
        "area_threshold": 500,
        "height_threshold": 2,
        "permit_required_text": "許可が必要：大牟田市手引き第4条参照。高さ2m以上または面積500㎡以上は許可申請が必要。",
        "no_permit_text": "許可不要：高さ・面積が基準未満である場合、届出のみ又は不要。",
        "page_line_info": "手引きP4 L3-L8",
        "procedure": "窓口：大牟田市、書類：盛土行為許可申請書、審査期間：14日、手数料：無料"
    }
    # Add more cities as needed...
}


def extract_geoname_from_text(text):
    # look for patterns
    patterns = [r'地名[:：]\s*([^\s,\n]+)',
                r'所在地[:：]\s*([^\s,\n]+)',
                r'対象地[:：]\s*([^\s,\n]+)']
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(1).strip()
    # fallback: search for typical city names (like containing 市)
    match2 = re.search(r'([\u4e00-\u9fff]+市[\u4e00-\u9fff]*)', text)
    if match2:
        return match2.group(1).strip()
    return None


def extract_area_from_text(text):
    # find numbers followed by ㎡ or m2
    match = re.search(r'([\d,.]+)\s*(?:㎡|m2)', text)
    if match:
        try:
            return float(match.group(1).replace(',', ''))
        except:
            return None
    return None


def extract_height_from_text(text):
    match = re.search(r'([\d,.]+)\s*m', text)
    if match:
        try:
            return float(match.group(1).replace(',', ''))
        except:
            return None
    return None


def parse_pdf(file):
    geoname = None
    area = None
    height = None
    if pdfplumber is None:
        return geoname, area, height
    try:
        with pdfplumber.open(file) as pdf:
            pages = pdf.pages
            text = ""
            for page in pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
            geoname = extract_geoname_from_text(text)
            area = extract_area_from_text(text)
            height = extract_height_from_text(text)
    except Exception as e:
        print(e)
    return geoname, area, height


def parse_dxf(file_bytes):
    geoname = None
    area = None
    height = None
    if ezdxf is None:
        return geoname, area, height
    try:
        doc = ezdxf.read(file_bytes)
        msp = doc.modelspace()
        total_area = 0.0
        for e in msp:
            if e.dxftype() == "HATCH":
                for path in e.paths:
                    try:
                        total_area += path.area
                    except Exception:
                        pass
            elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                # Only closed polylines
                try:
                    if e.closed:
                        total_area += e.area()
                except Exception:
                    pass
        if total_area > 0:
            area = total_area
    except Exception as e:
        print(e)
    return geoname, area, height


def evaluate_file(data):
    geoname = data.get("geoname")
    area = data.get("area")
    height = data.get("height")
    # Determine jurisdiction; default to Fukuoka Prefecture
    jurisdiction = "Fukuoka Prefecture"
    if geoname:
        for key in guidelines.keys():
            if key in geoname:
                jurisdiction = key
                break
    g = guidelines.get(jurisdiction, guidelines["Fukuoka Prefecture"])
    result = {}
    missing_info = []
    if geoname is None or geoname == "":
        missing_info.append("地名")
    if area is None or area == 0:
        missing_info.append("面積")
    if height is None or height == 0:
        missing_info.append("高さ")
    if missing_info:
        result["申請区分"] = "情報不足"
        result["改善案"] = None
        result["不足情報"] = "、".join(missing_info) + "の情報が不足しています"
        return result
    if area >= g["area_threshold"] or height >= g["height_threshold"]:
        result["申請区分"] = "許可申請"
        suggestions = []
        if area >= g["area_threshold"]:
            suggestions.append(f"造成面積を {g['area_threshold']}㎡ 未満に縮小する")
        if height >= g["height_threshold"]:
            suggestions.append(f"盛土高さを {g['height_threshold']}m 未満に抑える")
        result["改善案"] = "／".join(suggestions) if suggestions else None
        result["不足情報"] = None
    else:
        result["申請区分"] = "不要または届出"
        result["改善案"] = None
        result["不足情報"] = None
    return result


def generate_pdf_report(results):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    title = Paragraph("盛土規制法 判定レポート", styles["Title"])
    elements.append(title)
    elements.append(Spacer(1, 12))
    data = [["ファイル名", "地名", "面積 (㎡)", "高さ (m)", "申請区分", "改善案", "不足情報", "根拠"]]
    for res in results:
        jurisdiction = res.get("jurisdiction", "Fukuoka Prefecture")
        if res["申請区分"] == "許可申請":
            reason = f"{res['file']}は{jurisdiction}の基準により許可が必要です。{guidelines[jurisdiction]['permit_required_text']}根拠: {guidelines[jurisdiction]['page_line_info']}"
        elif res["申請区分"] == "不要または届出":
            reason = f"{res['file']}は{jurisdiction}の基準により許可は不要または届出です。{guidelines[jurisdiction]['no_permit_text']}根拠: {guidelines[jurisdiction]['page_line_info']}"
        else:
            reason = res.get("不足情報", "")
        data.append([
            res["file"],
            res.get("geoname") or "",
            f"{res.get('area'):.2f}" if isinstance(res.get("area"), (int, float)) else "",
            f"{res.get('height'):.2f}" if isinstance(res.get("height"), (int, float)) else "",
            res["申請区分"],
            res.get("改善案") or "",
            res.get("不足情報") or "",
            reason
        ])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.grey),
        ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("FONTNAME", (0,0), (-1,-1), "HeiseiMin-W3"),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,0), 6),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey)
    ]))
    elements.append(table)
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_excel_report(results):
    df = pd.DataFrame(results)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()


def main():
    st.set_page_config(page_title="盛土規制法 判定ツール（オンライン版）")
    st.title("盛土規制法 判定ツール（オンライン版）")
    st.write("PDF または DXF ファイルをアップロードすると、盛土規制法に基づく申請要否を自動判定し、改善案を提案します。")
    uploaded_files = st.file_uploader("ファイルをアップロードしてください（複数可）", type=["pdf", "dxf", "dwg", "jww"], accept_multiple_files=True)
    start_time = time.time()
    if uploaded_files:
        results = []
        progress_bar = st.progress(0.0)
        for idx, file in enumerate(uploaded_files):
            file_bytes = file.read()
            file.seek(0)
            geoname = None
            area = None
            height = None
            if file.name.lower().endswith(".pdf"):
                geoname, area, height = parse_pdf(file)
            elif file.name.lower().endswith(".dxf"):
                geoname, area, height = parse_dxf(file_bytes)
            # interactive input for missing values
            with st.expander(f"{file.name} の追加情報入力"):
                if not geoname:
                    geoname = st.text_input(f"{file.name} の地名を入力してください", key=f"geoname_{idx}")
                if not area:
                    area = st.number_input(f"{file.name} の面積 (㎡) を入力してください", min_value=0.0, format="%.2f", key=f"area_{idx}")
                if not height:
                    height = st.number_input(f"{file.name} の高さ (m) を入力してください", min_value=0.0, format="%.2f", key=f"height_{idx}")
            data = {
                "file": file.name,
                "geoname": geoname,
                "area": area,
                "height": height
            }
            result = evaluate_file(data)
            # Determine jurisdiction again for reasons
            jurisdiction = "Fukuoka Prefecture"
            if geoname:
                for key in guidelines.keys():
                    if key in geoname:
                        jurisdiction = key
                        break
            result.update(data)
            result["jurisdiction"] = jurisdiction
            results.append(result)
            # update progress
            progress_bar.progress((idx + 1) / len(uploaded_files))
            # show remaining time every 10 minutes
            elapsed = time.time() - start_time
            if elapsed >= 600 and (idx + 1) < len(uploaded_files):
                remaining = ((time.time() - start_time) / (idx + 1)) * (len(uploaded_files) - idx - 1)
                st.info(f"残り時間の目安: 約 {int(remaining//60)} 分 {int(remaining%60)} 秒")
        st.subheader("判定結果")
        df = pd.DataFrame(results)
        st.dataframe(df)
        excel_bytes = generate_excel_report(results)
        pdf_bytes = generate_pdf_report(results)
        st.download_button("Excel レポートをダウンロード", data=excel_bytes, file_name="morido_report.xlsx")
        st.download_button("PDF レポートをダウンロード", data=pdf_bytes, file_name="morido_report.pdf")
    else:
        st.info("ファイルをアップロードしてください。")


if __name__ == "__main__":
    main()
