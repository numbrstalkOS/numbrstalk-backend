"""
Numbrstalk.com - Diagnostic Intelligence Backend API v8.0
Reads data from local JSON files (updated hourly by GitHub Actions)
No outbound HTTP requests needed – works on Render free tier.
"""

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv
import io
import json
import os
import re
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path
from pydantic import BaseModel
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("data")
MAIN_JSON = DATA_DIR / "main.json"
CHANGE_DETECTION_JSON = DATA_DIR / "change_detection.json"
AI_INSIGHT_JSON = DATA_DIR / "ai_insight.json"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

# ============================================================================
# INITIALIZATION
# ============================================================================

app = FastAPI(title="Numbrstalk Diagnostic API", version="8.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

try:
    ai_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    ai_available = True
except:
    ai_client = None
    ai_available = False

main_data_cache: List[Dict] = []
change_data_cache: List[Dict] = []
ai_insight_cache: List[Dict] = []
last_refresh_time: Optional[datetime] = None

# ============================================================================
# MODELS
# ============================================================================

class ChatRequest(BaseModel):
    question: str
    category: Optional[str] = None

# ============================================================================
# DATA LOADERS
# ============================================================================

def load_json(filepath: Path) -> List[Dict]:
    if not filepath.exists():
        print(f"⚠️ {filepath} not found")
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"❌ Error loading {filepath}: {e}")
        return []

def refresh_all_caches():
    global main_data_cache, change_data_cache, ai_insight_cache, last_refresh_time
    main_data_cache = load_json(MAIN_JSON)
    change_data_cache = load_json(CHANGE_DETECTION_JSON)
    ai_insight_cache = load_json(AI_INSIGHT_JSON)
    last_refresh_time = datetime.now()
    print(f"✅ Caches refreshed: main={len(main_data_cache)}, changes={len(change_data_cache)}, insights={len(ai_insight_cache)}")

refresh_all_caches()

# ============================================================================
# HELPERS
# ============================================================================

def get_categories():
    return sorted(set(r.get('category', '').strip() for r in main_data_cache if r.get('category', '').strip()))

def get_brands():
    return sorted(set(r.get('brand', '').strip() for r in main_data_cache if r.get('brand', '').strip()))

def get_products(category=None, search=None):
    data = [r for r in main_data_cache if r.get('category', '').strip().lower() == category.lower()] if category else main_data_cache
    prods = {}
    for r in data:
        n = r.get('product_name', '').strip()
        if n and n not in prods:
            prods[n] = {"id": str(abs(hash(n)) % 100000), "name": n, "brand": r.get('brand', ''), "price": r.get('price', 0), "discount": r.get('discount', 0), "platform": r.get('platform', ''), "city": r.get('city', ''), "last_updated": r.get('scraped_at', '')}
    products = list(prods.values())
    if search:
        products = [p for p in products if search.lower() in p['name'].lower() or search.lower() in p['brand'].lower()]
    return products

def get_top_brands(limit=5):
    bd = defaultdict(list)
    for r in main_data_cache:
        b = r.get('brand', '').strip()
        d = r.get('discount', 0)
        if b and d > 0: bd[b].append(d)
    res = [{"brand": b, "avg_discount": round(sum(ds)/len(ds), 1), "product_count": len(ds)} for b, ds in bd.items()]
    res.sort(key=lambda x: x['avg_discount'], reverse=True)
    return res[:limit]

# ============================================================================
# CHANGE DETECTION PARSER
# ============================================================================

def parse_change_data():
    rows = change_data_cache
    result = {"total_changes": 0, "change_types": [], "keywords": [], "locations": [], "severity": {"Critical": 0, "High": 0, "Medium": 0}, "rank_drops": [], "rank_improvements": [], "new_entries": [], "disappeared": []}
    
    for row in rows:
        section = row.get('section', '').strip()
        content = row.get('content', '').strip()
        if section == 'Summary':
            m = re.search(r'Total changes detected: (\d+)', content)
            if m: result['total_changes'] = max(result['total_changes'], int(m.group(1)))
        elif section == 'Change type summary':
            for line in content.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    ct, c2 = line.rsplit(':', 1)
                    try: result['change_types'].append({"type": ct.strip().replace('- ', ''), "count": int(c2.strip())})
                    except: pass
        elif section == 'Most affected keywords':
            for line in content.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    kw, c2 = line.rsplit(':', 1)
                    try: result['keywords'].append({"keyword": kw.strip(), "changes": int(c2.strip().replace(' changes', ''))})
                    except: pass
        elif section == 'Most affected locations':
            for line in content.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    loc, c2 = line.rsplit(':', 1)
                    try: result['locations'].append({"location": loc.strip(), "changes": int(c2.strip().replace(' changes', ''))})
                    except: pass
        elif section == 'Severity summary':
            for line in content.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    sev, c2 = line.rsplit(':', 1)
                    try: result['severity'][sev.strip()] = int(c2.strip())
                    except: pass
    
    item_sections = {'Biggest rank drops': 'rank_drops', 'Biggest rank improvements': 'rank_improvements', 'New products entering top 10': 'new_entries', 'Products disappeared from top 10': 'disappeared'}
    for row in rows:
        section = row.get('section', '').strip()
        if section not in item_sections: continue
        content = row.get('content', '').strip()
        key = item_sections[section]
        items = re.split(r'\n(?=\d+\.)', content)
        for item in items:
            item = item.strip()
            if not item: continue
            item = re.sub(r'^\d+\.\s*', '', item)
            parts = [p.strip() for p in item.split('|')]
            if len(parts) >= 2:
                entry = {"location": parts[0], "keyword": parts[1] if len(parts) > 1 else "", "product": parts[2] if len(parts) > 2 else "", "issue": "", "old_rank": "?", "new_rank": "?", "old_price": "?", "new_price": "?"}
                m_issue = re.search(r'Issue:\s*(.+?)(?:\s*Rank:|\s*Price:|\s*Action:|$)', item)
                if m_issue: entry['issue'] = m_issue.group(1).strip()
                m_rank = re.search(r'Rank:\s*(\S+)\s*→\s*(\S+)', item)
                if m_rank: entry['old_rank'], entry['new_rank'] = m_rank.group(1), m_rank.group(2)
                result[key].append(entry)
    return result

# ============================================================================
# AI INSIGHTS
# ============================================================================

def generate_ai_insights():
    changes = parse_change_data()
    if changes['total_changes'] == 0 and not changes['change_types']:
        return {"headline": "📭 No marketplace changes detected yet", "summary": "Change detection data is empty. The GitHub Action may not have run yet.", "critical_issues": ["Run the GitHub Action manually from the Actions tab", "Ensure sheets are published"], "actions": [{"priority": "High", "action": "Trigger the fetch-sheets GitHub Action"}], "key_metrics": {"total_changes": 0, "critical_alerts": 0, "top_location": "No data", "main_risk": "No data"}}
    
    total = changes['total_changes']
    critical = changes['severity']['Critical']
    top_loc = changes['locations'][0] if changes['locations'] else {'location': 'N/A', 'changes': 0}
    top_kw = changes['keywords'][0] if changes['keywords'] else {'keyword': 'N/A', 'changes': 0}
    main_change = changes['change_types'][0] if changes['change_types'] else {'type': 'N/A', 'count': 0}
    
    return {
        "headline": f"📊 {total:,} marketplace changes tracked — {main_change['type']} is dominant",
        "summary": f"The latest scan detected {total:,} changes. {top_loc['location']} leads with {top_loc['changes']} changes.",
        "critical_issues": [f"{main_change['type']}: {main_change['count']} instances", f"{top_loc['location']}: {top_loc['changes']} changes", f"'{top_kw['keyword']}': {top_kw['changes']} changes"],
        "actions": [{"priority": "High", "action": f"Audit products in {top_loc['location']}"}, {"priority": "Medium", "action": f"Review '{top_kw['keyword']}' pricing"}],
        "key_metrics": {"total_changes": total, "critical_alerts": critical, "top_location": top_loc['location'], "main_risk": main_change['type']}
    }

# ============================================================================
# DIAGNOSIS ENGINE (simplified for v8.0)
# ============================================================================

SIGNAL_MAP = {"traffic": ["impressions", "clicks"], "product_page": ["product_views", "add_to_cart", "rating"], "cart": ["add_to_cart", "checkout_initiated"], "checkout": ["checkout_initiated", "payment_attempted", "cod_available"], "payment": ["payment_success_rate", "upi_failures"], "stock": ["in_stock_pct"], "competitor": ["comp_price_change"]}

def run_full_diagnosis(df):
    df.columns = df.columns.str.lower().str.strip()
    diagnosis = {"leak_stage": "Insufficient Data", "leak_stage_description": "Upload more funnel data", "reason_bucket": "Unknown", "evidence": [], "confidence": 0.0, "priority_actions": [], "signal_groups_found": [], "columns_analyzed": list(df.columns)}
    
    for group, cols in SIGNAL_MAP.items():
        if any(c in df.columns for c in cols): diagnosis["signal_groups_found"].append(group)
    
    if "add_to_cart" in df.columns and "checkout_initiated" in df.columns:
        atc = pd.to_numeric(df["add_to_cart"], errors='coerce').sum()
        ci = pd.to_numeric(df["checkout_initiated"], errors='coerce').sum()
        if atc > 0 and ci/atc < 0.4:
            diagnosis["leak_stage"] = "Cart"
            diagnosis["leak_stage_description"] = "People add to cart but don't checkout — cart friction likely"
            diagnosis["reason_bucket"] = "Cart Friction"
            diagnosis["confidence"] = 0.5
    
    if "payment_success_rate" in df.columns:
        ps = pd.to_numeric(df["payment_success_rate"], errors='coerce').mean()
        diagnosis["evidence"].append(f"Payment success: {ps:.0f}%")
        if ps < 70:
            diagnosis["leak_stage"] = "Payment"
            diagnosis["leak_stage_description"] = "Payment failures detected"
            diagnosis["reason_bucket"] = "Payment Gateway Issue"
            diagnosis["confidence"] = 0.6
    
    diagnosis["priority_actions"] = [{"priority": "High", "action": "Download the CSV template for correct column names"}] if not diagnosis["signal_groups_found"] else [{"priority": "High", "action": "Review the evidence above and check the AI Insights tab"}]
    diagnosis["confidence"] = min(diagnosis["confidence"], 0.85)
    return diagnosis

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "8.0", "data_rows": len(main_data_cache), "change_rows": len(change_data_cache), "ai_available": ai_available}

@app.get("/api/categories")
async def api_categories():
    return {"categories": get_categories(), "brands": get_brands()}

@app.get("/api/dashboard")
async def dashboard():
    return {"totalProducts": len(set(r.get('product_name', '') for r in main_data_cache)), "platforms": list(set(r.get('platform', '') for r in main_data_cache)), "lastUpdated": str(last_refresh_time)}

@app.get("/api/products")
async def api_products(category: Optional[str] = None, search: Optional[str] = None):
    products = get_products(category, search)
    return {"products": products[:100], "count": len(products)}

@app.get("/api/changes")
async def api_changes():
    try:
        return parse_change_data()
    except:
        return {"total_changes": 0, "change_types": [], "keywords": [], "locations": [], "severity": {"Critical": 0, "High": 0, "Medium": 0}, "rank_drops": [], "rank_improvements": [], "new_entries": [], "disappeared": []}

@app.get("/api/insights/ai")
async def api_ai_insights():
    return generate_ai_insights()

@app.get("/api/top-brands")
async def api_top_brands(limit: int = 5):
    return {"brands": get_top_brands(limit)}

@app.get("/api/reports/generate")
async def api_generate_report():
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Product", "Brand", "Price", "Discount", "Platform", "City"])
    for r in main_data_cache[:500]: w.writerow([r.get('product_name', ''), r.get('brand', ''), r.get('price', 0), r.get('discount', 0), r.get('platform', ''), r.get('city', '')])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=numbrstalk_report.csv"})

@app.get("/api/template/download")
async def download_template():
    columns = ["impressions", "clicks", "product_views", "add_to_cart", "checkout_initiated", "payment_attempted", "orders", "selling_price", "discount", "rating", "review_count", "delivery_charges", "cod_available", "payment_success_rate", "upi_failures", "in_stock_pct", "comp_price_change", "roas", "cpa"]
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(columns)
    w.writerow(["50000","5000","4500","200","80","60","39","500","10","4.2","120","60","yes","65","","85","","2.5",""])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=numbrstalk_template.csv"})

@app.post("/api/diagnose/upload")
async def diagnose_upload(file: UploadFile = File(...), category: Optional[str] = Form(None)):
    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content)) if file.filename.endswith('.csv') else pd.read_excel(io.BytesIO(content))
        diagnosis = run_full_diagnosis(df)
        diagnosis["filename"] = file.filename
        diagnosis["rows_analyzed"] = len(df)
        return diagnosis
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.post("/api/chat")
async def chat(request: ChatRequest):
    changes = parse_change_data()
    if changes['total_changes'] > 0:
        return {"answer": f"In the latest scan: {changes['total_changes']:,} changes. Top location: {changes['locations'][0]['location']}. Check AI Insights for details."}
    return {"answer": "I'm Lilly! Check the AI Insights tab for marketplace analysis. No recent scan data available yet."}

@app.post("/api/refresh")
async def refresh():
    refresh_all_caches()
    return {"status": "ok", "rows": len(main_data_cache)}

# ============================================================================
# SCHEDULER
# ============================================================================

scheduler = BackgroundScheduler()
scheduler.add_job(refresh_all_caches, 'interval', minutes=30)
scheduler.start()

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 Numbrstalk API v8.0 starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
