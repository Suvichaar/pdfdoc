import io
import os
import json
import base64
import hashlib
from typing import Optional, Any, Dict
from datetime import datetime
from pathlib import Path

import streamlit as st

# =========================
# PAGE SETUP
# =========================
st.set_page_config(page_title="PDF → DOCX Suvichaars", page_icon="📄", layout="wide")
st.title("📄 PDF → DOCX with SuvichaarDocument Intelligence")
st.caption(
    "Upload a PDF → SuvichaarDI (prebuilt-read) extracts text → Download a .docx • "
    "Pricing: ₹3 per page (3 credits) • Start balance: 30,000 credits ≈ 10,000 pages"
)

# =========================
# PRICING / CONSTANTS
# =========================
CREDITS_START_BALANCE = 30_000  # 30,000 credits ≈ 10,000 pages at 3 credits/page
PRICE_PER_PAGE_CREDITS = 3      # ₹3 == 3 credits

# persistent store file (server-side)
STORE_PATH = Path("./credits_store.json")

# =========================
# SECRETS / CONFIG
# =========================
def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return st.secrets[key]  # type: ignore[attr-defined]
    except Exception:
        return default

AZURE_DI_ENDPOINT = get_secret("AZURE_DI_ENDPOINT")
AZURE_DI_KEY = get_secret("AZURE_DI_KEY")
ADMIN_PIN = str(get_secret("ADMIN_PIN", "1133344444"))  # default fallback

# =========================
# SuvichaarSDK IMPORTS
# =========================
try:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
except Exception:
    DocumentIntelligenceClient = None
    AzureKeyCredential = None

try:
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest  # type: ignore
except Exception:
    AnalyzeDocumentRequest = None  # type: ignore

from docx import Document
from docx.shared import Pt

# =========================
# PERSISTENCE (STORE)
# =========================
DEFAULT_STORE: Dict[str, Any] = {
    "credits_balance": CREDITS_START_BALANCE,
    "charged_docs": {},   # file_hash -> {"pages": int, "cost": int, "file": str, "ts": str}
    "last_txn": None,     # {"file": str, "pages": int, "cost": int, "ts": str}
}

def load_store() -> Dict[str, Any]:
    if STORE_PATH.exists():
        try:
            with open(STORE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # sanity defaults
            for k, v in DEFAULT_STORE.items():
                data.setdefault(k, v)
            return data
        except Exception:
            # corrupted store → reset
            return DEFAULT_STORE.copy()
    else:
        return DEFAULT_STORE.copy()

def save_store(store: Dict[str, Any]) -> None:
    tmp = STORE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE_PATH)

# Load (or initialize) persistent store once per process
if "store" not in st.session_state:
    st.session_state.store = load_store()

# local aliases to store values (for readability)
def get_balance() -> int:
    return int(st.session_state.store.get("credits_balance", CREDITS_START_BALANCE))

def set_balance(val: int) -> None:
    st.session_state.store["credits_balance"] = int(val)
    save_store(st.session_state.store)

def get_charged_docs() -> Dict[str, Any]:
    return dict(st.session_state.store.get("charged_docs", {}))

def set_charged_docs(d: Dict[str, Any]) -> None:
    st.session_state.store["charged_docs"] = d
    save_store(st.session_state.store)

def get_last_txn() -> Optional[Dict[str, Any]]:
    return st.session_state.store.get("last_txn")

def set_last_txn(txn: Optional[Dict[str, Any]]) -> None:
    st.session_state.store["last_txn"] = txn
    save_store(st.session_state.store)

# =========================
# ADMIN SESSION FLAG
# =========================
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

# =========================
# SIDEBAR: CREDITS + ADMIN
# =========================
with st.sidebar:
    st.subheader("💳 Credits")
    max_display = max(CREDITS_START_BALANCE, get_balance())
    pct = min(max(get_balance() / float(max_display), 0.0), 1.0)
    st.progress(pct, text=f"Balance: {get_balance()} credits")
    st.caption("Start pool: 30,000 credits ≈ 10,000 pages")

    # Last transaction (pretty card)
    txn = get_last_txn()
    if txn:
        st.markdown(
            f"""
            <div style="
                background:#f5f8ff;
                padding:12px;
                border-radius:10px;
                border:1px solid #d1e3ff;
                margin-top:12px;
            ">
              <div style="font-weight:600;color:#1f4396;margin-bottom:6px;">🧾 Last Transaction</div>
              <div style="font-size:13px;line-height:1.4;">
                <div><b>File:</b> {txn['file']}</div>
                <div><b>Pages:</b> {txn['pages']}</div>
                <div><b>Credits:</b> {txn['cost']} (₹{txn['cost']})</div>
                <div style="color:#666;"><b>Time:</b> {txn.get('ts','')}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Admin panel (refill only here)
    with st.expander("🔐 Admin Panel", expanded=False):
        pin_entered = st.text_input("Enter Admin PIN", type="password", key="admin_pin_input")
        if st.button("Login", key="admin_login_btn"):
            if pin_entered == ADMIN_PIN:
                st.session_state.is_admin = True
                st.success("Login successful.")
            else:
                st.session_state.is_admin = False
                st.error("Wrong password")

        if st.session_state.is_admin:
            st.markdown("**Admin Controls**")
            topup_amt = st.number_input("Top-up amount (credits)", min_value=1, value=100, step=50, key="admin_topup_amount")
            if st.button("Top-up Wallet", key="admin_topup_btn"):
                set_balance(get_balance() + int(topup_amt))
                st.success(f"Wallet topped up by {int(topup_amt)} credits.")

            if st.button("Reset Wallet & History", key="admin_reset_btn"):
                # hard reset to defaults
                st.session_state.store = DEFAULT_STORE.copy()
                save_store(st.session_state.store)
                st.success("Wallet reset to 30,000 credits (≈ 10,000 pages) and history cleared.")

# =========================
# SETTINGS (single expander)
# =========================
with st.expander("⚙️ Settings", expanded=False):
    add_page_breaks = st.checkbox("Insert page breaks between PDF pages", value=True, key="opt_page_breaks")
    include_confidence = st.checkbox("Append line confidence (debug)", value=False, key="opt_conf")

# If secrets not configured, allow input for this run (unique keys)
if not AZURE_DI_ENDPOINT or not AZURE_DI_KEY:
    st.info("SuvichaarDI endpoint/key not found in st.secrets. Enter them for this session.")
    AZURE_DI_ENDPOINT = st.text_input(
        "AZURE_DI_ENDPOINT",
        AZURE_DI_ENDPOINT or "",
        placeholder="https://<resourcename>.cognitiveservices.azure.com/",
        key="endpoint_input",
    )
    AZURE_DI_KEY = st.text_input(
        "AZURE_DI_KEY",
        AZURE_DI_KEY or "",
        type="password",
        key="key_input",
    )

# Single uploader with a unique key
uploaded = st.file_uploader("Upload a PDF", type=["pdf"], accept_multiple_files=False, key="pdf_uploader_main")

# =========================
# HELPERS
# =========================
@st.cache_resource(show_spinner=False)
def make_client(endpoint: str, key: str):
    if DocumentIntelligenceClient is None or AzureKeyCredential is None:
        raise RuntimeError("SuvichaarSDK not installed. Run: pip install azure-ai-documentintelligence python-docx")
    if not endpoint or not key:
        raise RuntimeError("Missing SuvichaarDI endpoint or key.")
    return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def analyze_pdf_bytes(client: Any, pdf_bytes: bytes):
    """
    SuvichaarDI 'prebuilt-read' across multiple SDK variants.
    """
    last_err = None
    try:
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            document=pdf_bytes,
            content_type="application/pdf",
        )
        return poller.result()
    except Exception as e:
        last_err = e
    try:
        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        poller = client.begin_analyze_document(
            "prebuilt-read",
            body={"base64Source": b64},
        )
        return poller.result()
    except Exception as e:
        last_err = e
    try:
        if AnalyzeDocumentRequest is not None:
            req = AnalyzeDocumentRequest(bytes_source=pdf_bytes)  # type: ignore
            poller = client.begin_analyze_document(
                model_id="prebuilt-read",
                body=req,
                content_type="application/pdf",
            )
            return poller.result()
    except Exception as e:
        last_err = e
    raise last_err

def result_to_docx_bytes(result, insert_page_breaks: bool = True, show_conf: bool = False) -> bytes:
    """Convert DI 'prebuilt-read' result into a .docx."""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    if not getattr(result, "pages", None):
        doc.add_paragraph(getattr(result, "content", "") or "No content found.")
    else:
        for idx, page in enumerate(result.pages):
            doc.add_heading(f"Page {idx+1}", level=2)
            if getattr(page, "lines", None):
                for ln in page.lines:
                    text = ln.content or ""
                    if show_conf and hasattr(ln, "spans") and ln.spans:
                        try:
                            confs = [getattr(s, "confidence", None) for s in ln.spans if getattr(s, "confidence", None) is not None]
                            if confs:
                                text += f"  [conf~{sum(confs)/len(confs):.2f}]"
                        except Exception:
                            pass
                    if text.strip():
                        doc.add_paragraph(text)
            else:
                paras = []
                for p in getattr(result, "paragraphs", []) or []:
                    if getattr(p, "spans", None):
                        if any(getattr(sp, "offset", None) is not None for sp in p.spans):
                            paras.append(p.content)
                if paras:
                    for p in paras:
                        doc.add_paragraph(p)
                else:
                    doc.add_paragraph(getattr(result, "content", "") or "")

            if insert_page_breaks and idx < len(result.pages) - 1:
                doc.add_page_break()

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()

def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def charge_credits_once(file_id: str, pages: int, filename: str) -> int:
    """Deduct credits exactly once per unique file hash (persisted)."""
    cost = pages * PRICE_PER_PAGE_CREDITS

    charged_docs = get_charged_docs()
    if file_id in charged_docs:
        st.info("⚠️ This file was already processed earlier. No credits deducted again.")
        return 0

    if get_balance() < cost:
        raise RuntimeError(
            f"Insufficient credits: need {cost}, have {get_balance()}. "
            "Please ask an admin to top-up credits."
        )

    # Deduct and persist
    set_balance(get_balance() - cost)
    charged_docs[file_id] = {
        "pages": pages,
        "cost": cost,
        "file": filename,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    set_charged_docs(charged_docs)

    set_last_txn({
        "file": filename,
        "pages": pages,
        "cost": cost,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    return cost

# =========================
# MAIN FLOW
# =========================
if uploaded is not None:
    if not uploaded.name.lower().endswith(".pdf"):
        st.error("Please upload a PDF file.")
    else:
        try:
            client = make_client(AZURE_DI_ENDPOINT or "", AZURE_DI_KEY or "")
        except Exception as e:
            st.error(f"Failed to create SuvichaarDI client: {e}")
            st.stop()

        pdf_bytes = uploaded.read()
        if not pdf_bytes:
            st.error("Uploaded file is empty. Please re-upload the PDF.")
            st.stop()

        fid = file_hash(pdf_bytes)

        with st.spinner("Analyzing with SuvichaarDocument Intelligence (prebuilt-read)..."):
            try:
                result = analyze_pdf_bytes(client, pdf_bytes)
            except Exception as e:
                st.error(f"SuvichaarDI analyze failed: {e}")
                st.stop()

        pages = len(getattr(result, "pages", []) or [])
        if pages <= 0:
            pages = 1
        st.success(f"Extracted text from **{pages} page(s)**.")

        try:
            charged = charge_credits_once(fid, pages, uploaded.name)
            if charged > 0:
                st.toast(f"Charged {charged} credits for {pages} page(s).", icon="✅")
        except RuntimeError as e:
            st.error(str(e))
            st.stop()

        with st.spinner("Building DOCX..."):
            try:
                docx_bytes = result_to_docx_bytes(
                    result,
                    insert_page_breaks=st.session_state.get("opt_page_breaks", True),
                    show_conf=st.session_state.get("opt_conf", False)
                )
            except Exception as e:
                st.error(f"Failed to create DOCX: {e}")
                st.stop()

        st.download_button(
            label="⬇️ Download .docx",
            data=docx_bytes,
            file_name=(uploaded.name.rsplit(".", 1)[0] + ".docx"),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="download_docx_btn"
        )
else:
    st.info("Upload a PDF to begin.")

# =========================
# FOOTER
# =========================
st.caption(
    "Credits persist across reloads in a server-side JSON store. Only admins can refill using the PIN from st.secrets. "
    "Start pool: 30,000 credits ≈ 10,000 pages. "
    f"Pricing: {PRICE_PER_PAGE_CREDITS} credits (₹{PRICE_PER_PAGE_CREDITS}) per page."
)
