"""
Numbrstalk.com - Diagnostic Intelligence Backend API v8.1
Reads data from local JSON files (updated by GitHub Actions)
"""
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv, io, json, os, re, pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path
from pydantic import BaseModel
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from collections import defaultdict

DATA_DIR = Path("data")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

app = FastAPI(title="Numbrstalk Diagnostic API", version="8.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

try:
    ai_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    ai_available = True
except:
    ai_client = None
    ai_available = False

main_data_cache, change_data_cache, ai_insight_cache = [], [], []
last_refresh_time = None

class ChatRequest(BaseModel):
    question: str
    category: Optional[str] = None

def load_json(fp):
    if not fp.exists(): return []
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []

def refresh_all_caches():
    global main_data_cache, change_data_cache, ai_insight_cache, last_refresh_time
    main_data_cache = load_json(DATA_DIR / "main.json")
    change_data_cache = load_json(DATA_DIR / "change_detection.json")
    ai_insight_cache = load_json(DATA_DIR / "ai_insight.json")
    last_refresh_time = datetime.now()

refresh_all_caches()

def get_categories():
    return sorted(set(str(r.get('category','')).strip() for r in main_data_cache if str(r.get('category','')).strip()))

def get_brands():
    return sorted(set(str(r.get('brand','')).strip() for r in main_data_cache if str(r.get('brand','')).strip()))

def get_products(category=None, search=None):
    data = [r for r in main_data_cache if str(r.get('category','')).strip().lower() == str(category).lower()] if category else main_data_cache
    prods = {}
    for r in data:
        n = str(r.get('product_name','')).strip()
        if n and n not in prods:
            price = r.get('price', 0)
            discount = r.get('discount', 0)
            try: price = float(price)
            except: price = 0
            try: discount = float(str(discount).replace('%',''))
            except: discount = 0
            prods[n] = {"id":str(abs(hash(n))%100000),"name":n,"brand":str(r.get('brand','')),"price":price,"discount":discount,"platform":str(r.get('platform','')),"city":str(r.get('city','')),"last_updated":str(r.get('scraped_at',''))}
    products = list(prods.values())
    if search:
        products = [p for p in products if search.lower() in p['name'].lower() or search.lower() in p['brand'].lower()]
    return products

def get_top_brands(limit=5):
    try:
        bd = defaultdict(list)
        for r in main_data_cache:
            b = str(r.get('brand', '')).strip()
            d = r.get('discount', 0)
            try: d = float(str(d).replace('%', '').strip())
            except: d = 0
            if b and d > 0: bd[b].append(d)
        res = [{"brand": b, "avg_discount": round(sum(ds)/len(ds), 1), "product_count": len(ds)} for b, ds in bd.items()]
        res.sort(key=lambda x: x['avg_discount'], reverse=True)
        return res[:limit] if res else [{"brand": "No data", "avg_discount": 0, "product_count": 0}]
    except Exception as e:
        return [{"brand": "Error", "avg_discount": 0, "product_count": 0}]

def parse_change_data():
    rows = change_data_cache
    result = {"total_changes":0,"change_types":[],"keywords":[],"locations":[],"severity":{"Critical":0,"High":0,"Medium":0},"rank_drops":[],"rank_improvements":[],"new_entries":[],"disappeared":[]}
    for row in rows:
        s = str(row.get('section','')).strip()
        c = str(row.get('content','')).strip()
        if 'Summary' in s:
            m = re.search(r'Total changes detected:\s*(\d+)', c)
            if m: result['total_changes'] = max(result['total_changes'], int(m.group(1)))
        if 'Change type' in s:
            for line in c.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    try: result['change_types'].append({"type": parts[0].strip().replace('- ',''), "count": int(parts[1].strip())})
                    except: pass
        if 'keyword' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    try: result['keywords'].append({"keyword": parts[0].strip(), "changes": int(parts[1].strip().replace(' changes',''))})
                    except: pass
        if 'location' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    try: result['locations'].append({"location": parts[0].strip(), "changes": int(parts[1].strip().replace(' changes',''))})
                    except: pass
        if 'Severity' in s:
            for line in c.split('\n'):
                line = line.strip('- *').strip()
                if ':' in line:
                    parts = line.rsplit(':', 1)
                    try: result['severity'][parts[0].strip()] = int(parts[1].strip())
                    except: pass
        if 'rank drop' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = re.sub(r'^\d+\.\s*', '', item.strip())
                parts = [p.strip() for p in item.split('|')]
                if len(parts) >= 2:
                    entry = {"location": parts[0], "keyword": parts[1] if len(parts)>1 else "", "product": parts[2] if len(parts)>2 else "", "old_rank": "?", "new_rank": "?"}
                    m = re.search(r'Rank:\s*(\S+)\s*→\s*(\S+)', item)
                    if m: entry['old_rank'], entry['new_rank'] = m.group(1), m.group(2)
                    result['rank_drops'].append(entry)
        if 'rank improv' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = re.sub(r'^\d+\.\s*', '', item.strip())
                parts = [p.strip() for p in item.split('|')]
                if len(parts) >= 2:
                    entry = {"location": parts[0], "keyword": parts[1] if len(parts)>1 else "", "product": parts[2] if len(parts)>2 else "", "old_rank": "?", "new_rank": "?"}
                    m = re.search(r'Rank:\s*(\S+)\s*→\s*(\S+)', item)
                    if m: entry['old_rank'], entry['new_rank'] = m.group(1), m.group(2)
                    result['rank_improvements'].append(entry)
        if 'new product' in s.lower() or 'entering' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = re.sub(r'^\d+\.\s*', '', item.strip())
                parts = [p.strip() for p in item.split('|')]
                if len(parts) >= 2:
                    entry = {"location": parts[0], "keyword": parts[1] if len(parts)>1 else "", "product": parts[2] if len(parts)>2 else "", "new_rank": "?"}
                    m = re.search(r'Rank:\s*-\s*→\s*(\S+)', item)
                    if m: entry['new_rank'] = m.group(1)
                    result['new_entries'].append(entry)
        if 'disappeared' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = re.sub(r'^\d+\.\s*', '', item.strip())
                parts = [p.strip() for p in item.split('|')]
                if len(parts) >= 2:
                    entry = {"location": parts[0], "keyword": parts[1] if len(parts)>1 else "", "product": parts[2] if len(parts)>2 else "", "old_rank": "?"}
                    result['disappeared'].append(entry)
    return result

def generate_ai_insights():
    changes = parse_change_data()
    if changes['total_changes'] == 0:
        return {"headline":"📭 No marketplace changes detected yet","summary":"Change detection data is empty.","critical_issues":["Run the fetch-sheets GitHub Action"],"actions":[{"priority":"High","action":"Run GitHub Action"}],"key_metrics":{"total_changes":0,"critical_alerts":0,"top_location":"No data","main_risk":"No data"}}
    total = changes['total_changes']
    top_loc = changes['locations'][0] if changes['locations'] else {'location':'N/A','changes':0}
    top_kw = changes['keywords'][0] if changes['keywords'] else {'keyword':'N/A','changes':0}
    mc = changes['change_types'][0] if changes['change_types'] else {'type':'N/A','count':0}
    return {"headline":f"📊 {total:,} changes — {mc['type']} is dominant","summary":f"{total:,} changes. {top_loc['location']} leads. '{top_kw['keyword']}' most volatile.","critical_issues":[f"{mc['type']}: {mc['count']}",f"{top_loc['location']}: {top_loc['changes']}",f"'{top_kw['keyword']}': {top_kw['changes']}"],"actions":[{"priority":"High","action":f"Audit {top_loc['location']}"},{"priority":"Medium","action":f"Review '{top_kw['keyword']}' pricing"}],"key_metrics":{"total_changes":total,"critical_alerts":changes['severity']['Critical'],"top_location":top_loc['location'],"main_risk":mc['type']}}

SIGNAL_MAP = {"traffic":["impressions","clicks"],"product_page":["product_views","add_to_cart","rating"],"cart":["add_to_cart","checkout_initiated"],"checkout":["checkout_initiated","payment_attempted","cod_available"],"payment":["payment_success_rate","upi_failures"],"stock":["in_stock_pct"],"competitor":["comp_price_change"]}

def run_full_diagnosis(df):
    df.columns = df.columns.str.lower().str.strip()
    d = {"leak_stage":"Insufficient Data","leak_stage_description":"Upload more funnel data","reason_bucket":"Unknown","evidence":[],"confidence":0.0,"priority_actions":[],"signal_groups_found":[],"columns_analyzed":list(df.columns)}
    for g,cs in SIGNAL_MAP.items():
        if any(c in df.columns for c in cs): d["signal_groups_found"].append(g)
    d["priority_actions"] = [{"priority":"High","action":"Review evidence and check AI Insights tab"}]
    return d

# ── API ──
@app.get("/api/health")
async def health():
    return {"status":"ok","version":"8.1","data_rows":len(main_data_cache),"change_rows":len(change_data_cache),"ai_available":ai_available}

@app.get("/api/categories")
async def api_categories():
    return {"categories":get_categories(),"brands":get_brands()}

@app.get("/api/dashboard")
async def dashboard():
    return {"totalProducts":len(set(str(r.get('product_name','')) for r in main_data_cache)),"platforms":list(set(str(r.get('platform','')) for r in main_data_cache)),"lastUpdated":str(last_refresh_time)}

@app.get("/api/products")
async def api_products(category:Optional[str]=None,search:Optional[str]=None):
    return {"products":get_products(category,search)[:100],"count":len(get_products(category,search))}

@app.get("/api/changes")
async def api_changes():
    try: return parse_change_data()
    except: return {"total_changes":0}

@app.get("/api/insights/ai")
async def api_ai_insights():
    return generate_ai_insights()

@app.get("/api/top-brands")
async def api_top_brands(limit:int=5):
    return {"brands":get_top_brands(limit)}

@app.get("/api/reports/generate")
async def api_generate_report():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["Product","Brand","Price","Discount","Platform","City"])
    for r in main_data_cache[:500]: w.writerow([str(r.get('product_name','')),str(r.get('brand','')),r.get('price',0),r.get('discount',0),str(r.get('platform','')),str(r.get('city',''))])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=numbrstalk_report.csv"})

@app.get("/api/template/download")
async def download_template():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["impressions","clicks","product_views","add_to_cart","checkout_initiated","payment_attempted","orders","selling_price","discount","rating","delivery_charges","cod_available","payment_success_rate","in_stock_pct","comp_price_change","roas"])
    w.writerow(["50000","5000","4500","200","80","60","39","500","10","4.2","60","yes","65","85","","2.5"])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=numbrstalk_template.csv"})

@app.post("/api/diagnose/upload")
async def diagnose_upload(file:UploadFile=File(...)):
    content=await file.read()
    df=pd.read_csv(io.BytesIO(content)) if file.filename.endswith('.csv') else pd.read_excel(io.BytesIO(content))
    d=run_full_diagnosis(df); d["filename"]=file.filename; d["rows_analyzed"]=len(df)
    return d

@app.post("/api/chat")
async def chat(request:ChatRequest):
    changes=parse_change_data()
    if changes['total_changes']>0:
        return {"answer":f"Latest scan: {changes['total_changes']:,} changes. Check AI Insights for details."}
    return {"answer":"I'm Lilly! No recent scan data. Check the AI Insights tab or upload a CSV."}

scheduler=BackgroundScheduler()
scheduler.add_job(refresh_all_caches,'interval',minutes=30)
scheduler.start()

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)
