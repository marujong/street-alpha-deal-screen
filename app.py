from dataclasses import dataclass
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="Street Alpha | Deal Screen", page_icon="◼", layout="wide")

st.markdown("""
<style>
:root {
  --bg:#F6F1E8; --card:#FFFDFC; --text:#1F1F1F;
  --muted:#6B625E; --accent:#7A2432; --line:#E6DED2;
}
.stApp { background: var(--bg); color: var(--text); }
.block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }
h1,h2,h3 { letter-spacing:-0.03em; }
div[data-testid="stMetric"] {
  background:var(--card); border:1px solid var(--line);
  padding:14px 16px; border-radius:14px;
}
.sa-card {
  background:var(--card); border:1px solid var(--line);
  border-radius:16px; padding:18px 20px; margin:8px 0 16px;
}
.sa-kicker { color:var(--accent); font-weight:800; font-size:.82rem; letter-spacing:.08em; }
.sa-muted { color:var(--muted); }
.sa-score { font-size:4rem; font-weight:900; line-height:1; letter-spacing:-.06em; }
.sa-grade {
  display:inline-block; padding:5px 10px; border-radius:999px;
  background:#EFE2E3; color:var(--accent); font-weight:800; margin-left:8px;
}

/* S1 가독성 보완: 기존 배치·색상 토큰을 유지하고 텍스트 대비만 보정합니다. */
.stApp [data-testid="stMetricLabel"],
.stApp [data-testid="stMetricLabel"] *,
.stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricValue"] *,
.stApp [data-testid="stWidgetLabel"],
.stApp [data-testid="stWidgetLabel"] * {
  color: var(--text) !important;
}
.stApp [data-testid="stAlert"] [data-testid="stMarkdownContainer"],
.stApp [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
.stApp [data-testid="stAlert"] [data-testid="stMarkdownContainer"] span,
.stApp [data-testid="stAlert"] [data-testid="stMarkdownContainer"] strong,
.stApp [data-testid="stAlert"] [data-testid="stMarkdownContainer"] li {
  color: var(--text) !important;
}
/* 보조 버튼은 기존 카드색/본문색을 짝지어 밝은/어두운 테마 모두에서 읽히게 합니다. */
.stApp [data-testid="stButton"] button[kind="secondary"],
.stApp [data-testid="stButton"] button[data-testid="stBaseButton-secondary"] {
  background-color: var(--card) !important;
  color: var(--text) !important;
  border-color: var(--line) !important;
}
.stApp [data-testid="stButton"] button[kind="secondary"] *,
.stApp [data-testid="stButton"] button[data-testid="stBaseButton-secondary"] * {
  color: inherit !important;
}
.stApp [data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled),
.stApp [data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
  border-color: var(--accent) !important;
}
.stApp [data-testid="stButton"] button[kind="secondary"]:focus-visible,
.stApp [data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
""", unsafe_allow_html=True)

API_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"
SOURCE_ID = "D01"
SOURCE_LABEL = "소상공인시장진흥공단 상가(상권)정보 API"
REFERENCE_PERIOD_LABEL = "미확인"
SUCCESS_CODES = {"00", "0", "0000"}
PRESET_LOCATIONS = {
    "성수역": (127.0561, 37.5446),
    "강남역": (127.0276, 37.4979),
    "홍대입구역": (126.9240, 37.5572),
    "을지로3가역": (126.9921, 37.5663),
    "여의도역": (126.9245, 37.5217),
    "직접 좌표 입력": None,
}


class StoreAPIError(RuntimeError):
    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind = kind
        self.code = code


def safe_text(v):
    return "" if v is None else str(v).strip()


def get_api_key():
    try:
        return st.secrets.get("DATA_GO_KR_API_KEY", "")
    except Exception:
        return ""


def normalize_items(data):
    if not isinstance(data, dict):
        raise StoreAPIError("schema_error", "공급사 응답 형식이 예상과 다릅니다.")

    root = data.get("response")
    if root is None and "header" in data and "body" in data:
        root = data
    if not isinstance(root, dict):
        raise StoreAPIError("schema_error", "공급사 응답에 response 구조가 없습니다.")

    header = root.get("header")
    body = root.get("body")
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise StoreAPIError("schema_error", "공급사 응답의 header/body 구조를 확인할 수 없습니다.")

    raw_code = header.get("resultCode", header.get("resultcode"))
    if raw_code is None:
        raise StoreAPIError("schema_error", "공급사 응답에 결과 코드가 없습니다.")
    result_code = str(raw_code)
    if result_code not in SUCCESS_CODES:
        raise StoreAPIError("provider_error", f"공공데이터 공급사 오류 ({result_code})", code=result_code)

    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", [])
    if items is None:
        items = []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise StoreAPIError("schema_error", "공급사 응답의 매장 목록 형식이 예상과 다릅니다.")

    total_count = body.get("totalCount", len(items))
    try:
        total_count = int(total_count)
    except (TypeError, ValueError):
        raise StoreAPIError("schema_error", "공급사 응답의 전체 건수를 해석할 수 없습니다.")
    if total_count < 0:
        raise StoreAPIError("schema_error", "공급사 응답의 전체 건수가 올바르지 않습니다.")
    return items, total_count


def classify_http_status(status_code):
    if status_code in (401, 403):
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    return "provider_error"


def public_error_message(exc):
    messages = {
        "permission_error": "공공데이터 조회 권한 오류입니다. 인증키 값은 화면에 표시하지 않았습니다.",
        "rate_limit_error": "공공데이터 호출 한도에 도달했거나 일시적으로 제한되었습니다.",
        "network_error": "공공데이터 공급사 연결에 실패했습니다. 잠시 뒤 다시 시도하세요.",
        "schema_error": "공공데이터 응답 구조를 해석하지 못했습니다. 오류를 정상 0건으로 처리하지 않았습니다.",
        "provider_error": "공공데이터 공급사 오류로 조회하지 못했습니다.",
    }
    text = messages.get(getattr(exc, "kind", None), "공공데이터 조회 중 알 수 없는 오류가 발생했습니다.")
    if getattr(exc, "code", None):
        text += f" 오류 코드: {exc.code}"
    return text


@st.cache_data(ttl=300, show_spinner=False)
def fetch_stores_in_radius(api_key, lon, lat, radius, max_items=500):
    all_items = []
    page = 1
    rows = 100
    total_count = None

    while len(all_items) < max_items:
        params = {
            "ServiceKey": api_key,
            "pageNo": page,
            "numOfRows": rows,
            "radius": int(radius),
            "cx": float(lon),
            "cy": float(lat),
            "type": "json",
        }
        try:
            response = requests.get(API_URL, params=params, timeout=15)
        except requests.Timeout as exc:
            raise StoreAPIError("network_error", "공급사 응답 시간이 초과되었습니다.") from exc
        except requests.RequestException as exc:
            raise StoreAPIError("network_error", "공급사 연결 요청에 실패했습니다.") from exc

        if not response.ok:
            kind = classify_http_status(response.status_code)
            raise StoreAPIError(kind, "공급사 HTTP 오류", code=str(response.status_code))

        try:
            data = response.json()
        except ValueError as exc:
            raise StoreAPIError("schema_error", "공급사가 JSON이 아닌 응답을 반환했습니다.") from exc

        items, page_total = normalize_items(data)
        if total_count is None:
            total_count = page_total
        all_items.extend(items)
        if not items or len(all_items) >= page_total or len(items) < rows:
            break
        page += 1
        if page > 10:
            break

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    loaded_items = all_items[:max_items]
    total_count = total_count if total_count is not None else len(loaded_items)
    return loaded_items, total_count, {
        "retrieved_at": retrieved_at,
        "pages_requested": page,
        "partial": len(loaded_items) < total_count,
    }


def normalize_coordinate(value, lower, upper):
    if safe_text(value) == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not lower <= number <= upper:
        return None
    return number


def store_to_row(item):
    lon_raw = item.get("lon", "")
    lat_raw = item.get("lat", "")
    lon = normalize_coordinate(lon_raw, -180, 180)
    lat = normalize_coordinate(lat_raw, -90, 90)
    coord_missing = safe_text(lon_raw) == "" or safe_text(lat_raw) == ""
    coord_status = "미확인" if coord_missing else ("정상" if lon is not None and lat is not None else "오류")
    return {
        "상호명": safe_text(item.get("bizesNm")),
        "지점명": safe_text(item.get("brchNm")),
        "업종 대분류": safe_text(item.get("indsLclsNm")),
        "업종 중분류": safe_text(item.get("indsMclsNm")),
        "업종 소분류": safe_text(item.get("indsSclsNm")),
        "도로명주소": safe_text(item.get("rdnmAdr")),
        "지번주소": safe_text(item.get("lnoAdr")),
        "경도": lon,
        "위도": lat,
        "좌표상태": coord_status,
        "상가업소번호": safe_text(item.get("bizesId")),
    }


def dedupe_rows_by_store_id(rows):
    seen = set()
    unique = []
    duplicate_count = 0
    for row in rows:
        store_id = row.get("상가업소번호", "")
        if store_id and store_id in seen:
            duplicate_count += 1
            continue
        if store_id:
            seen.add(store_id)
        unique.append(row)
    return unique, duplicate_count


def missing_public_fields(row):
    missing = []
    if not row.get("상호명"):
        missing.append("상호명")
    if not (row.get("도로명주소") or row.get("지번주소")):
        missing.append("주소")
    if not (row.get("업종 소분류") or row.get("업종 중분류") or row.get("업종 대분류")):
        missing.append("업종")
    if not row.get("상가업소번호"):
        missing.append("원본 ID")
    if row.get("좌표상태") != "정상":
        missing.append("좌표")
    return missing


def competition_level_from_count(count):
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 10:
        return 3
    if count <= 20:
        return 4
    return 5


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def linear_score(value, bad, good):
    if good == bad:
        return 50.0
    return clamp((value - bad) / (good - bad) * 100)


def grade_from_score(score):
    if score >= 85: return "A"
    if score >= 75: return "B+"
    if score >= 65: return "B"
    if score >= 55: return "C+"
    if score >= 45: return "C"
    return "D"


@dataclass
class Inputs:
    store_name: str
    category: str
    address: str
    years_operated: float
    monthly_revenue: float
    revenue_growth: float
    cogs: float
    labor: float
    rent: float
    other_costs: float
    asking_price: float
    owner_hours: float
    manager_exists: bool
    sop_level: int
    lease_months: int
    competition_level: int
    demand_stability: int
    capacity_headroom: int
    digital_gap: int
    customer_concentration: int
    evidence_quality: int


def calculate_scores(x):
    operating_profit = x.monthly_revenue - x.cogs - x.labor - x.rent - x.other_costs
    margin = (operating_profit / x.monthly_revenue * 100) if x.monthly_revenue > 0 else -100
    annual_profit = max(0, operating_profit * 12)
    payback_years = (x.asking_price / annual_profit) if annual_profit > 0 else 99
    rent_ratio = (x.rent / x.monthly_revenue * 100) if x.monthly_revenue > 0 else 100

    financial = (
        0.40 * linear_score(margin, 0, 25)
        + 0.20 * linear_score(x.revenue_growth, -10, 15)
        + 0.25 * linear_score(payback_years, 6, 2)
        + 0.15 * linear_score(rent_ratio, 20, 7)
    )
    commercial = (
        0.35 * linear_score(x.years_operated, 1, 10)
        + 0.30 * linear_score(x.competition_level, 5, 1)
        + 0.35 * linear_score(x.demand_stability, 1, 5)
    )
    independence = (
        0.45 * linear_score(x.owner_hours, 70, 10)
        + 0.25 * (100 if x.manager_exists else 25)
        + 0.30 * linear_score(x.sop_level, 1, 5)
    )
    growth = (
        0.45 * linear_score(x.capacity_headroom, 1, 5)
        + 0.35 * linear_score(x.digital_gap, 1, 5)
        + 0.20 * linear_score(x.revenue_growth, -10, 15)
    )
    risk_control = (
        0.35 * linear_score(x.lease_months, 6, 60)
        + 0.25 * linear_score(x.customer_concentration, 5, 1)
        + 0.40 * linear_score(x.evidence_quality, 1, 5)
    )

    scores = {
        "재무 안정성": clamp(financial),
        "상권·수요 경쟁력": clamp(commercial),
        "대표자 독립성": clamp(independence),
        "성장 가능성": clamp(growth),
        "리스크 통제": clamp(risk_control),
    }
    weights = {
        "재무 안정성": 0.30,
        "상권·수요 경쟁력": 0.25,
        "대표자 독립성": 0.20,
        "성장 가능성": 0.15,
        "리스크 통제": 0.10,
    }
    overall = sum(scores[k] * weights[k] for k in scores)
    metrics = {
        "영업이익": operating_profit,
        "영업이익률": margin,
        "연환산 영업이익": annual_profit,
        "단순 회수기간": payback_years,
        "임차료 비중": rent_ratio,
        "종합점수": overall,
    }
    return scores, metrics


def generate_findings(x, m):
    strengths, risks, questions = [], [], []
    if m["영업이익률"] >= 15:
        strengths.append(f"영업이익률 {m['영업이익률']:.1f}%로 수익성이 비교적 양호합니다.")
    if x.years_operated >= 7:
        strengths.append(f"{x.years_operated:.0f}년 운영 이력으로 장기 생존 신호가 있습니다.")
    if x.owner_hours <= 30:
        strengths.append("대표자 주당 투입시간이 낮아 운영 전환 부담이 상대적으로 작습니다.")
    if x.manager_exists:
        strengths.append("별도 관리자 체계가 있어 대표자 교체 이후 운영 연속성에 유리합니다.")
    if x.revenue_growth >= 5:
        strengths.append(f"최근 매출 성장률 입력값이 {x.revenue_growth:.1f}%로 성장 흐름이 있습니다.")

    if m["영업이익률"] < 8:
        risks.append(f"영업이익률이 {m['영업이익률']:.1f}%로 낮아 비용 구조 검증이 필요합니다.")
    if m["단순 회수기간"] > 5:
        risks.append(f"입력 가격 기준 단순 회수기간이 약 {m['단순 회수기간']:.1f}년으로 깁니다.")
    if x.owner_hours >= 50:
        risks.append(f"대표자가 주 {x.owner_hours:.0f}시간 근무해 대표자 의존도가 높을 수 있습니다.")
    if x.lease_months < 24:
        risks.append(f"임대차 잔여기간이 {x.lease_months}개월로 짧아 재계약 조건 확인이 중요합니다.")
    if x.customer_concentration >= 4:
        risks.append("특정 고객·채널 의존도가 높게 입력되어 매출 이탈 위험 점검이 필요합니다.")
    if x.evidence_quality <= 2:
        risks.append("매출·비용 증빙 수준이 낮아 현재 점수의 신뢰도가 제한됩니다.")
    if x.competition_level >= 4:
        risks.append("경쟁 강도가 높은 것으로 입력되어 주변 경쟁점의 변화를 확인해야 합니다.")

    if x.evidence_quality < 5:
        questions.append("최근 24개월 POS 매출, 카드매출, 부가세 신고자료가 서로 일치하는가?")
    if x.lease_months < 60:
        questions.append("임대인과 재계약 가능 여부, 보증금·임대료 인상 조건은 무엇인가?")
    if not x.manager_exists:
        questions.append("대표자가 빠졌을 때 발주·정산·직원관리·고객응대를 누가 맡을 수 있는가?")
    if x.sop_level < 4:
        questions.append("핵심 업무를 매뉴얼화할 수 있고 인수자에게 인계 가능한가?")
    questions.append("설비 교체, 원상복구, 미지급금 등 인수 직후 발생할 일회성 비용은 얼마인가?")

    if not strengths:
        strengths.append("현재 입력값만으로 뚜렷한 강점 신호가 부족합니다. 원자료 확보 후 재평가가 필요합니다.")
    if not risks:
        risks.append("큰 경고 신호는 적지만 실제 인수 전 원자료 검증은 별도로 필요합니다.")
    return strengths, risks, questions


def fmt_money(v):
    return f"{v:,.0f}만원"


DEFAULTS = {
    "store_name": "샘플 PC방",
    "category": "PC방",
    "address": "서울특별시 성동구",
    "competition_level": 3,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.markdown('<div class="sa-kicker">STREET ALPHA / DEAL SCREEN</div>', unsafe_allow_html=True)
st.title("작은 가게를, 인수 가능한 사업체처럼 분석합니다.")
st.caption("MVP v0.3.1 · S1 매장 검색·공개 기본정보 카드 + 사용자 입력 기반 1차 스크리닝")

st.subheader("0. 실제 상가 공공데이터 불러오기")
st.caption("소상공인시장진흥공단 상가(상권)정보 API에서 실제 영업 중 상가의 상호명·업종·주소·좌표를 조회합니다. 매출·임대료·인건비는 공개되지 않으므로 별도 입력이 필요합니다.")

api_key = get_api_key()
if not api_key:
    st.error("Streamlit Secrets에 DATA_GO_KR_API_KEY가 없습니다. Manage app → Settings → Secrets에서 키를 저장하세요.")
else:
    location_col, radius_col = st.columns([2, 1])
    location_name = location_col.selectbox("기준 위치", list(PRESET_LOCATIONS.keys()))
    radius = radius_col.select_slider("조회 반경", options=[200, 300, 500, 800, 1000, 1500, 2000], value=500, format_func=lambda x: f"{x}m")

    if location_name == "직접 좌표 입력":
        c1, c2 = st.columns(2)
        lon = c1.number_input("경도", value=127.0276, format="%.6f")
        lat = c2.number_input("위도", value=37.4979, format="%.6f")
    else:
        lon, lat = PRESET_LOCATIONS[location_name]
        st.caption(f"기준 좌표 · 경도 {lon:.4f} / 위도 {lat:.4f}")

    if st.button("공공데이터 불러오기", type="primary", use_container_width=True):
        st.session_state.pop("public_error", None)
        try:
            with st.spinner("실제 상가 데이터를 불러오는 중입니다..."):
                stores, total_count, fetch_meta = fetch_stores_in_radius(api_key, lon, lat, radius)
            st.session_state["public_stores"] = stores
            st.session_state["public_total"] = total_count
            st.session_state["public_radius"] = radius
            st.session_state["public_location_name"] = location_name
            st.session_state["public_fetch_meta"] = fetch_meta
            if stores:
                st.success(f"연결 성공 · 반경 {radius}m 내 공급사 전체 건수 {total_count:,}개 중 {len(stores):,}개를 불러왔습니다.")
            else:
                st.info("조회는 정상 처리됐지만 이 조건에 해당하는 매장이 없습니다.")
        except StoreAPIError as exc:
            st.session_state["public_stores"] = []
            st.session_state["public_total"] = 0
            st.session_state["public_fetch_meta"] = {}
            st.session_state["public_error"] = exc.kind
            st.error(public_error_message(exc))
        except Exception:
            st.session_state["public_stores"] = []
            st.session_state["public_total"] = 0
            st.session_state["public_fetch_meta"] = {}
            st.session_state["public_error"] = "unknown_error"
            st.error("공공데이터 조회 중 알 수 없는 오류가 발생했습니다. 인증키나 전체 요청 URL은 화면에 표시하지 않았습니다.")

    stores = st.session_state.get("public_stores", [])
    if stores:
        raw_rows = [store_to_row(x) for x in stores]
        rows, duplicate_count = dedupe_rows_by_store_id(raw_rows)
        total_count = st.session_state.get("public_total", len(raw_rows))
        fetch_meta = st.session_state.get("public_fetch_meta", {})
        partial = bool(fetch_meta.get("partial", len(raw_rows) < total_count))
        invalid_coords = sum(1 for r in rows if r["좌표상태"] == "오류")
        missing_coords = sum(1 for r in rows if r["좌표상태"] == "미확인")

        if partial:
            st.warning(
                f"부분 결과입니다. 공급사 전체 건수는 {total_count:,}개로 표시됐지만 현재 화면에는 최대 {len(raw_rows):,}개만 불러왔습니다. "
                "아래 필터와 동종업종 수는 이 불러온 범위 안에서만 계산됩니다."
            )
        if duplicate_count:
            st.warning(f"같은 상가업소번호가 중복된 {duplicate_count:,}건을 화면 목록에서 한 번만 표시했습니다.")
        if invalid_coords or missing_coords:
            st.caption(f"좌표 품질 · 오류 {invalid_coords:,}건 / 미확인 {missing_coords:,}건")

        keyword = st.text_input("매장명·주소·업종으로 결과 필터", placeholder="예: PC방, 카페, 성수")
        filtered = rows
        if keyword.strip():
            needle = keyword.strip().lower()
            filtered = [r for r in rows if needle in " ".join(safe_text(v) for v in r.values()).lower()]

        m1, m2, m3 = st.columns(3)
        m1.metric("불러온 고유 업소", f"{len(rows):,}개")
        m2.metric("필터 결과", f"{len(filtered):,}개")
        unique_small = len({r["업종 소분류"] for r in filtered if r["업종 소분류"]})
        m3.metric("업종 소분류", f"{unique_small:,}개")

        if filtered:
            display_columns = [
                "상호명", "지점명", "업종 대분류", "업종 중분류", "업종 소분류",
                "도로명주소", "지번주소", "상가업소번호", "경도", "위도", "좌표상태"
            ]
            st.dataframe([{k: r.get(k) for k in display_columns} for r in filtered], use_container_width=True, hide_index=True, height=320)
            selectable = filtered[:200]
            if len(filtered) > len(selectable):
                st.caption(f"선택 목록은 현재 필터 결과 {len(filtered):,}개 중 앞 {len(selectable):,}개만 표시합니다.")

            labels = []
            for i, row in enumerate(selectable):
                category = row["업종 소분류"] or row["업종 중분류"] or row["업종 대분류"] or "업종 미확인"
                address = row["도로명주소"] or row["지번주소"] or "주소 미확인"
                name = row["상호명"] or "상호 미확인"
                branch = f" {row['지점명']}" if row["지점명"] else ""
                labels.append(f"{i+1}. {name}{branch} · {category} · {address}")

            selected_label = st.selectbox("진단할 매장 선택", labels)
            selected_idx = labels.index(selected_label)
            selected = selectable[selected_idx]
            selected_small = selected["업종 소분류"]
            same_category_count = sum(1 for r in rows if selected_small and r["업종 소분류"] == selected_small)
            missing_fields = missing_public_fields(selected)
            address = selected["도로명주소"] or selected["지번주소"] or "미확인"
            category = selected_small or selected["업종 중분류"] or selected["업종 대분류"] or "미확인"
            coverage_text = (
                f"{st.session_state.get('public_location_name', '선택 위치')} 반경 {st.session_state.get('public_radius', 0)}m · "
                f"{len(raw_rows):,}/{total_count:,}건 불러옴"
            )
            if partial:
                coverage_text += " · 부분 결과"
            else:
                coverage_text += " · 현재 응답 범위 전체"

            st.markdown("#### 공개 기본정보 카드")
            with st.container(border=True):
                st.markdown(f"### {selected['상호명'] or '상호 미확인'}{(' · ' + selected['지점명']) if selected['지점명'] else ''}")
                c1, c2 = st.columns(2)
                with c1:
                    st.write("**업종**", category)
                    st.write("**주소**", address)
                    st.write("**상가업소번호**", selected["상가업소번호"] or "미확인")
                    if selected["좌표상태"] == "정상":
                        st.write("**좌표**", f"{selected['위도']:.6f}, {selected['경도']:.6f}")
                    else:
                        st.write("**좌표**", f"{selected['좌표상태']}")
                with c2:
                    st.write("**출처**", f"{SOURCE_ID} · {SOURCE_LABEL}")
                    st.write("**자료 기준일**", REFERENCE_PERIOD_LABEL)
                    st.write("**수집 시각(UTC)**", fetch_meta.get("retrieved_at", "미확인"))
                    st.write("**수집 범위**", coverage_text)
                    st.write("**데이터 성격**", "공개 관측값")

                if missing_fields:
                    st.warning("확인하지 못한 기본정보: " + ", ".join(missing_fields))
                else:
                    st.caption("기본카드 필수 공개 필드는 현재 선택 항목에서 모두 확인됐습니다.")
                st.info("매출·임대료·인건비·희망 인수가격·대표자 의존도는 이 공개자료에서 확인되지 않습니다. 아래 진단 입력값과 구분해서 봐야 합니다.")
                st.caption("자료 기준일은 현재 사용 중인 D01 응답 필드에서 확인하지 못해 임의 날짜 대신 '미확인'으로 표시합니다.")

            st.info(
                f"선택 매장: **{selected['상호명'] or '상호 미확인'}** · {category}  |  "
                f"현재 불러온 고유 업소 {len(rows):,}개 안에서 같은 소분류 **{same_category_count}개**"
            )
            if partial:
                st.caption("위 같은 소분류 개수는 전체 반경 업소 수가 아니라 현재 불러온 부분 결과 기준입니다. 점수의 경쟁 강도로 자동 반영하지 않습니다.")

            if st.button("선택 매장으로 아래 진단 시작", use_container_width=True):
                st.session_state["store_name"] = selected["상호명"] or "선택 매장"
                st.session_state["category"] = category if category != "미확인" else "기타"
                st.session_state["address"] = address if address != "미확인" else ""
                st.session_state["selected_public_store"] = True
                st.session_state["selected_public_store_id"] = selected["상가업소번호"]
                if not partial:
                    st.session_state["competition_level"] = competition_level_from_count(same_category_count)
                st.rerun()
        else:
            st.warning("필터 조건과 일치하는 매장이 없습니다. 이 메시지는 API 오류와 구분되는 정상 '검색 결과 없음' 상태입니다.")

st.divider()

with st.form("deal_form"):
    st.subheader("1. 매장 기본 정보")
    c1, c2, c3 = st.columns([1.2, 1.2, 2.0])
    store_name = c1.text_input("매장명", key="store_name")
    category = c2.text_input("업종", key="category")
    address = c3.text_input("주소", key="address")
    years_operated = st.slider("운영기간(년)", 0.0, 30.0, 8.0, 0.5)

    st.subheader("2. 재무")
    if st.session_state.get("selected_public_store"):
        st.warning("아래 재무·운영 기본값은 기존 MVP의 예시값이며 선택한 실제 매장의 공개자료가 아닙니다. 실제 자료로 바꾸기 전 점수를 해당 매장의 진단 결과로 해석하지 마세요.")
    a, b, c, d, e = st.columns(5)
    monthly_revenue = a.number_input("월매출(만원)", min_value=0.0, value=4200.0, step=100.0)
    cogs = b.number_input("원가/재료비(만원)", min_value=0.0, value=900.0, step=50.0)
    labor = c.number_input("인건비(만원)", min_value=0.0, value=850.0, step=50.0)
    rent = d.number_input("임차료(만원)", min_value=0.0, value=350.0, step=10.0)
    other_costs = e.number_input("기타 월비용(만원)", min_value=0.0, value=700.0, step=50.0)

    a, b, c = st.columns(3)
    asking_price = a.number_input("희망 인수가격(만원)", min_value=0.0, value=12000.0, step=500.0)
    revenue_growth = b.number_input("최근 12개월 매출 성장률(%)", value=4.0, step=1.0)
    evidence_quality = c.select_slider(
        "증빙 신뢰도", options=[1,2,3,4,5], value=3,
        format_func=lambda v: ["구두 추정","일부 자료","기본 증빙","대부분 검증","원자료 완비"][v-1]
    )

    st.subheader("3. 운영 구조")
    a, b, c, d = st.columns(4)
    owner_hours = a.slider("대표 주당 근무시간", 0, 100, 45)
    manager_exists = b.toggle("별도 관리자 있음", value=False)
    sop_level = c.select_slider(
        "업무 매뉴얼화", options=[1,2,3,4,5], value=3,
        format_func=lambda v: ["거의 없음","낮음","보통","높음","매우 높음"][v-1]
    )
    lease_months = d.slider("임대차 잔여기간(개월)", 0, 120, 36, step=6)

    st.subheader("4. 시장·성장·리스크")
    a, b, c, d = st.columns(4)
    competition_level = a.select_slider(
        "경쟁 강도", options=[1,2,3,4,5], key="competition_level",
        format_func=lambda v: ["매우 낮음","낮음","보통","높음","매우 높음"][v-1]
    )
    demand_stability = b.select_slider(
        "수요 안정성", options=[1,2,3,4,5], value=3,
        format_func=lambda v: ["매우 낮음","낮음","보통","높음","매우 높음"][v-1]
    )
    capacity_headroom = c.select_slider(
        "추가 매출 여지", options=[1,2,3,4,5], value=3,
        format_func=lambda v: ["거의 없음","낮음","보통","높음","매우 높음"][v-1]
    )
    digital_gap = d.select_slider(
        "운영·마케팅 개선 여지", options=[1,2,3,4,5], value=4,
        format_func=lambda v: ["거의 없음","낮음","보통","높음","매우 높음"][v-1]
    )
    customer_concentration = st.select_slider(
        "특정 고객/채널 의존도", options=[1,2,3,4,5], value=2,
        format_func=lambda v: ["매우 낮음","낮음","보통","높음","매우 높음"][v-1]
    )

    submitted = st.form_submit_button("Street Alpha Score 계산", use_container_width=True, type="primary")

if submitted:
    x = Inputs(
        store_name, category, address, years_operated, monthly_revenue, revenue_growth,
        cogs, labor, rent, other_costs, asking_price, owner_hours, manager_exists,
        sop_level, lease_months, competition_level, demand_stability,
        capacity_headroom, digital_gap, customer_concentration, evidence_quality
    )
    scores, metrics = calculate_scores(x)
    strengths, risks, questions = generate_findings(x, metrics)

    st.divider()
    st.markdown('<div class="sa-kicker">RESULT</div>', unsafe_allow_html=True)
    left, right = st.columns([1.1, 2])

    with left:
        score = metrics["종합점수"]
        grade = grade_from_score(score)
        card = (
            '<div class="sa-card">'
            f'<div class="sa-muted">{x.store_name} · {x.category}</div>'
            '<div style="margin-top:8px">'
            f'<span class="sa-score">{score:.0f}</span><span style="font-size:1.2rem"> / 100</span>'
            f'<span class="sa-grade">{grade}</span></div>'
            f'<div class="sa-muted" style="margin-top:10px">{x.address}</div>'
            '</div>'
        )
        st.markdown(card, unsafe_allow_html=True)
        st.metric("월 추정 영업이익", fmt_money(metrics["영업이익"]))
        st.metric("영업이익률", f"{metrics['영업이익률']:.1f}%")
        st.metric("단순 투자금 회수기간", f"{metrics['단순 회수기간']:.1f}년" if metrics["단순 회수기간"] < 90 else "산정 불가")

    with right:
        st.subheader("평가 항목")
        for label, val in scores.items():
            col_a, col_b = st.columns([4, 1])
            col_a.progress(int(val), text=label)
            col_b.markdown(f"**{val:.0f}**")
        st.caption("가중치: 재무 30% · 상권/수요 25% · 대표자 독립성 20% · 성장 15% · 리스크 통제 10%")

    st.subheader("강점 / 리스크")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**확인된 강점**")
        for item in strengths:
            st.success(item)
    with c2:
        st.markdown("**우선 확인할 리스크**")
        for item in risks:
            st.warning(item)

    st.subheader("실사 전 추가 질문")
    for idx, q in enumerate(questions, 1):
        st.markdown(f"**{idx}.** {q}")

    score = metrics["종합점수"]
    grade = grade_from_score(score)
    st.subheader("간단 인수 검토 요약")
    summary = (
        f"**{x.store_name}**은 현재 입력값 기준 Street Alpha Score **{score:.0f}/100 ({grade})**입니다.  \n"
        f"월매출 **{fmt_money(x.monthly_revenue)}**, 추정 영업이익 **{fmt_money(metrics['영업이익'])}**, "
        f"영업이익률 **{metrics['영업이익률']:.1f}%**이며, 희망 인수가격 **{fmt_money(x.asking_price)}** 기준 "
        f"단순 회수기간은 **{metrics['단순 회수기간']:.1f}년**입니다.\n\n"
        "현재 결과는 공공 상가정보와 사용자 입력값, 내부 휴리스틱에 기반한 1차 스크리닝입니다. "
        "실제 인수 검토에서는 POS·세무자료·임대차계약·인건비·설비 교체비·대표자 대체 가능성을 원자료로 확인해야 합니다."
    )
    st.info(summary)

    report_lines = [
        "STREET ALPHA DEAL SCREEN", "", f"매장명: {x.store_name}", f"업종: {x.category}", f"주소: {x.address}", "",
        f"종합점수: {score:.0f}/100 ({grade})", "", "[핵심 지표]", f"월매출: {fmt_money(x.monthly_revenue)}",
        f"월 추정 영업이익: {fmt_money(metrics['영업이익'])}", f"영업이익률: {metrics['영업이익률']:.1f}%",
        f"단순 회수기간: {metrics['단순 회수기간']:.1f}년", "", "[항목별 점수]",
    ]
    report_lines += [f"- {k}: {v:.0f}/100" for k, v in scores.items()]
    report_lines += ["", "[강점]"] + [f"- {s}" for s in strengths]
    report_lines += ["", "[리스크]"] + [f"- {r}" for r in risks]
    report_lines += ["", "[추가 질문]"] + [f"- {q}" for q in questions]
    report_lines += ["", "주의: 본 결과는 MVP 휴리스틱 기반 1차 스크리닝이며 투자·법률·세무 자문이 아닙니다."]

    st.download_button(
        "결과 TXT 다운로드",
        data="\n".join(report_lines).encode("utf-8"),
        file_name="street_alpha_deal_screen.txt",
        mime="text/plain",
        use_container_width=True,
    )
