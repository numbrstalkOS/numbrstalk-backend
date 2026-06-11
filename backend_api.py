"""
Numbrstalk.com - Diagnostic Intelligence Backend API v8.2
"""
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv, io, json, os, re
import pandas as pd
from datetime import datetime
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from collections import defaultdict

DATA_DIR = Path("data")

app = FastAPI(title="Numbrstalk API", version="8.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

main_data, change_data, insight_data = [], [], []
last_refresh = None

class ChatRequest(BaseModel):
    question: str

def load_json(fp):
    if not fp.exists(): return []
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []

def refresh():
    global main_data, change_data, insight_data, last_refresh
    main_data = load_json(DATA_DIR / "main.json")
    change_data = load_json(DATA_DIR / "change_detection.json")
    insight_data = load_json(DATA_DIR / "ai_insight.json")
    last_refresh = datetime.now()

refresh()

def safe_float(v, default=0):
    try: return float(str(v).replace('%','').replace('₹','').replace(',','').strip())
    except: return default

def get_products():
    prods = {}
    for r in main_data:
        n = str(r.get('product_name','')).strip()
        if n and n not in prods:
            prods[n] = {
                "id": str(abs(hash(n))%100000),
                "name": n,
                "brand": str(r.get('brand','')),
                "price": safe_float(r.get('price')),
                "discount": safe_float(r.get('discount')),
                "platform": str(r.get('platform','')),
                "city": str(r.get('city',''))
            }
    return list(prods.values())

def get_top_brands(limit=5):
    bd = defaultdict(list)
    for r in main_data:
        b = str(r.get('brand','')).strip()
        d = safe_float(r.get('discount'))
        if b and d > 0: bd[b].append(d)
    res = [{"brand":b,"avg_discount":round(sum(ds)/len(ds),1),"product_count":len(ds)} for b,ds in bd.items()]
    res.sort(key=lambda x:x['avg_discount'], reverse=True)
    return res[:limit] if res else [{"brand":"No data","avg_discount":0,"product_count":0}]

def parse_changes():
    result = {"total_changes":0,"change_types":[],"keywords":[],"locations":[],"severity":{"Critical":0,"High":0,"Medium":0},"rank_drops":[],"rank_improvements":[],"new_entries":[],"disappeared":[]}
    for row in change_data:
        s = str(row.get('section','')).strip()
        c = str(row.get('content','')).strip()
        m = re.search(r'Total changes detected:\s*(\d+)', c)
        if m: result['total_changes'] = max(result['total_changes'], int(m.group(1)))
        if 'change type' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *•').strip()
                if ':' in line:
                    parts = line.rsplit(':',1)
                    try: result['change_types'].append({"type":parts[0].strip(),"count":int(parts[1].strip())})
                    except: pass
        if 'keyword' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *•').strip()
                if ':' in line:
                    parts = line.rsplit(':',1)
                    try: result['keywords'].append({"keyword":parts[0].strip(),"changes":int(parts[1].strip().replace('changes','').strip())})
                    except: pass
        if 'location' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *•').strip()
                if ':' in line:
                    parts = line.rsplit(':',1)
                    try: result['locations'].append({"location":parts[0].strip(),"changes":int(parts[1].strip().replace('changes','').strip())})
                    except: pass
        if 'severity' in s.lower():
            for line in c.split('\n'):
                line = line.strip('- *•').strip()
                if ':' in line:
                    parts = line.rsplit(':',1)
                    try: result['severity'][parts[0].strip()] = int(parts[1].strip())
                    except: pass
        if 'rank drop' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = item.strip()
                if not item: continue
                entry = {"product":item[:120],"old_rank":"?","new_rank":"?"}
                m = re.search(r'Rank:\s*(\S+)\s*→\s*(\S+)', item)
                if m: entry['old_rank'], entry['new_rank'] = m.group(1), m.group(2)
                result['rank_drops'].append(entry)
        if 'rank improv' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = item.strip()
                if not item: continue
                entry = {"product":item[:120],"old_rank":"?","new_rank":"?"}
                m = re.search(r'Rank:\s*(\S+)\s*→\s*(\S+)', item)
                if m: entry['old_rank'], entry['new_rank'] = m.group(1), m.group(2)
                result['rank_improvements'].append(entry)
        if 'new product' in s.lower() or 'entering' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = item.strip()
                if not item: continue
                entry = {"product":item[:120],"new_rank":"?"}
                m = re.search(r'Rank:\s*-\s*→\s*(\S+)', item)
                if m: entry['new_rank'] = m.group(1)
                result['new_entries'].append(entry)
        if 'disappeared' in s.lower():
            for item in re.split(r'\n(?=\d+\.)', c):
                item = item.strip()
                if not item: continue
                result['disappeared'].append({"product":item[:120]})
    return result

def ai_insights():
    c = parse_changes()
    if c['total_changes']==0:
        return {"headline":"📭 No changes detected","summary":"Run GitHub Action to fetch sheet data.","key_metrics":{"total_changes":0,"critical_alerts":0,"top_location":"N/A","main_risk":"N/A"},"critical_issues":[],"actions":[{"priority":"High","action":"Run fetch-sheets GitHub Action"}]}
    tl = c['locations'][0] if c['locations'] else {'location':'N/A','changes':0}
    tk = c['keywords'][0] if c['keywords'] else {'keyword':'N/A','changes':0}
    mc = c['change_types'][0] if c['change_types'] else {'type':'N/A','count':0}
    return {
        "headline":f"📊 {c['total_changes']:,} changes — {mc['type']} dominant",
        "summary":f"{c['total_changes']:,} changes. {tl['location']} leads. '{tk['keyword']}' most volatile.",
        "key_metrics":{"total_changes":c['total_changes'],"critical_alerts":c['severity']['Critical'],"top_location":tl['location'],"main_risk":mc['type']},
        "critical_issues":[f"{mc['type']}: {mc['count']}",f"{tl['location']}: {tl['changes']}",f"'{tk['keyword']}': {tk['changes']}"],
        "actions":[{"priority":"High","action":f"Audit {tl['location']}"},{"priority":"Medium","action":f"Review '{tk['keyword']}'"}]
    }

@app.get("/api/health")
async def health():
    return {"status":"ok","version":"8.2","data_rows":len(main_data),"change_rows":len(change_data)}

@app.get("/api/categories")
async def categories():
    cats = sorted(set(str(r.get('category','')).strip() for r in main_data if str(r.get('category','')).strip()))
    brands = sorted(set(str(r.get('brand','')).strip() for r in main_data if str(r.get('brand','')).strip()))
    return {"categories":cats,"brands":brands}

@app.get("/api/dashboard")
async def dashboard():
    return {"totalProducts":len(get_products()),"lastUpdated":str(last_refresh)}

@app.get("/api/products")
async def products():
    return {"products":get_products()[:100],"count":len(get_products())}

@app.get("/api/changes")
async def changes():
    return parse_changes()

@app.get("/api/insights/ai")
async def insights_ai():
    return ai_insights()

@app.get("/api/top-brands")
async def top_brands(limit:int=5):
    return {"brands":get_top_brands(limit)}

@app.get("/api/reports/generate")
async def report():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["Product","Brand","Price","Discount","Platform","City"])
    for p in get_products()[:500]: w.writerow([p['name'],p['brand'],p['price'],p['discount'],p['platform'],p['city']])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=report.csv"})

@app.get("/api/template/download")
async def template():
    output=io.StringIO(); w=csv.writer(output)
    w.writerow(["impressions","clicks","product_views","add_to_cart","checkout_initiated","payment_success_rate","in_stock_pct","selling_price","discount","rating","cod_available","roas"])
    w.writerow(["50000","5000","4500","200","80","65","85","500","10","4.2","yes","2.5"])
    output.seek(0)
    return StreamingResponse(output,media_type="text/csv",headers={"Content-Disposition":"attachment; filename=template.csv"})

@app.post("/api/diagnose/upload")
async def diagnose_upload(file:UploadFile=File(...)):
    content=await file.read()
    df=pd.read_csv(io.BytesIO(content)) if file.filename.endswith('.csv') else pd.read_excel(io.BytesIO(content))
    return {"filename":file.filename,"rows":len(df),"leak_stage":"Analysis pending","signal_groups_found":list(df.columns)}

@app.post("/api/chat")
async def chat(request:ChatRequest):
    return {"answer":"I'm Lilly! Check the AI Insights tab for marketplace analysis, or upload a CSV for instant diagnosis."}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)