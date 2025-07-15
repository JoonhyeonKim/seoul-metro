# streamlit_app.py
import os, re, requests, xmltodict, pandas as pd, streamlit as st
from dotenv import load_dotenv
from difflib import get_close_matches

# ───────── 1. API 키 로드 (.env → secrets.toml) ─────────
load_dotenv()

def get_api_key():
    env_key = os.getenv("SEOUL_API_KEY")
    if env_key:
        return env_key
    try:
        return st.secrets["SEOUL_API_KEY"]
    except Exception:
        return None

API_KEY = get_api_key()
if not API_KEY:
    st.error("🚨 API 키가 없습니다. .env 또는 Streamlit Secrets를 설정하세요.")
    st.stop()

# ───────── 2. XML 파싱 + 999건씩 반복 호출 ─────────
def parse_xml(text: str):
    p = xmltodict.parse(text)
    root = next(iter(p))
    rows = p[root].get("row", [])
    rows = [rows] if isinstance(rows, dict) else rows
    total = int(p[root]["list_total_count"])
    return rows, total

def fetch_xml_full(endpoint: str, chunk: int = 999) -> pd.DataFrame:
    base = f"http://openapi.seoul.go.kr:8088/{API_KEY}/xml/{endpoint}"
    rows, total = parse_xml(requests.get(f"{base}/1/1/").text)
    for start in range(2, total + 1, chunk):
        end = min(start + chunk - 1, total)
        add, _ = parse_xml(requests.get(f"{base}/{start}/{end}/").text)
        rows.extend(add)
    return pd.DataFrame(rows)

# ───────── 3. 데이터 캐시 (1 h) ─────────
@st.cache_data(ttl=3600)
def load_all():
    return (
        fetch_xml_full("TbSubwayLineDetail",   chunk=99),   # 폐쇄(23)
        fetch_xml_full("SeoulMetroFaciInfo"),               # 승강기(2867)
        fetch_xml_full("SmrtScnFcltsInfo"),                 # 길이(2721)
        fetch_xml_full("TbSeoulmetroStConve"),              # 편의시설(290)
    )

# ───────── 4. 역 이름 정규화 + 매핑 ─────────
def norm(name: str) -> str:
    return re.sub(r"\(.*?\)", "", str(name)).replace("역", "").strip()

def build_map(dfs):
    raw = set()
    for df in dfs:
        for col in ("STN_NM", "SBWY_STNS_NM", "STATION_NAME"):
            if col in df.columns:
                raw.update(df[col].dropna())
    n2r = {}
    for r in raw:
        n2r.setdefault(norm(r), []).append(r)
    return n2r

# ───────── 5. UI ─────────
st.title("🚇 서울 지하철 역별 편의 정보 통합 뷰어")
query = st.text_input("역 이름을 입력하세요:")

if query:
    df_close, df_status, df_depth, df_conv = load_all()
    nmap = build_map([df_close, df_status, df_depth, df_conv])

    key = norm(query)
    if key in nmap:
        targets = nmap[key]
    else:
        close = get_close_matches(key, nmap.keys(), n=1, cutoff=0.4)
        targets = nmap.get(close[0], []) if close else []

    if not targets:
        st.error("❌ 해당 역 데이터가 없습니다.")
        st.stop()

    st.success("대상 역: " + ", ".join(targets))

    # 🔒 폐쇄
    st.subheader("🔒 출입구·휠체어리프트 임시 폐쇄")
    if "SBWY_STNS_NM" in df_close.columns:
        show_c = df_close[df_close["SBWY_STNS_NM"].isin(targets)]
        if show_c.empty:
            st.info("폐쇄 정보 없음")
        else:
            st.dataframe(show_c[["CLSG_PLC","BGNG_YMD","END_YMD","RPLC_PATH"]],
                         use_container_width=True)
    # ⚠️ 승강기 가동
    st.subheader("⚠️ 승강기·에스컬레이터 가동")
    if "STN_NM" in df_status.columns:
        show_s = df_status[df_status["STN_NM"].isin(targets)]
        if show_s.empty:
            st.info("정보 없음")
        else:
            bad = show_s[show_s["USE_YN"]!="사용가능"]
            if bad.empty:
                st.success("🟢 모든 시설 정상 작동")
            else:
                st.warning("🚫 작동 중지 시설")
                st.dataframe(bad[["ELVTR_NM","OPR_SEC","INSTL_PSTN","USE_YN"]],
                             use_container_width=True)
    # 📏 길이
    st.subheader("📏 승강기·에스컬레이터 길이")
    if "SBWY_STNS_NM" in df_depth.columns:
        show_d = df_depth[df_depth["SBWY_STNS_NM"].isin(targets)]
        if show_d.empty:
            st.info("정보 없음")
        else:
            out = show_d[["EQPMNT","NO","PLF_PBADMS","OPR_SEC"]].copy()
            out["PLF_PBADMS"] = out["PLF_PBADMS"].apply(
                lambda v: f"{int(v)//1000} m" if str(v).isdigit() else "-"
            )
            st.dataframe(out, use_container_width=True)
    # 🛗 편의시설
    st.subheader("🛗 편의시설")
    if "STATION_NAME" in df_conv.columns:
        show_v = df_conv[df_conv["STATION_NAME"].isin(targets)]
        if show_v.empty:
            st.info("정보 없음")
        else:
            row = show_v.iloc[0]
            fmap = {"EL":"엘리베이터","WL":"휠체어리프트","PARKING":"주차장",
                    "BICYCLE":"자전거보관소","CIM":"무인민원발급기","EXCHANGE":"환전소",
                    "TRAIN":"열차매표","CULTURE":"문화공간","PLACE":"유휴공간","FDROOM":"수유실"}
            avail = [v for k,v in fmap.items() if row.get(k)=="Y"]
            st.write("✔️ 이용 가능: " + ", ".join(avail) if avail else "❌ 이용 가능 시설 없음")