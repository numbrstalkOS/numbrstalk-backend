"""
Numbrstalk.com - Diagnostic Intelligence Backend API v8.3
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

app = FastAPI(title="Numbrstalk API", version="8.3.0")
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

def parse_discount(v):
    """Parse discount from strings like '46% OFF', '15%', etc."""
    try:
        s = str(v).replace('% OFF','').replace('%','').replace('OFF','').strip()
        return float(s)
    except:
        return 0

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
                "discount": parse_discount(r.get('discount','')),
                "platform": str(r.get('platform','')),
                "city": str(r.get('city',''))
            }
    return list(prods.values())

def get_top_brands(limit=5):
    bd = defaultdict(list)
    for r in main_data:
        b = str(r.get('brand','')).strip()
        d = parse_discount(r.get('discount',''))
        if b and d > 0: bd[b].append(d)
    res = [{"brand":b,"avg_discount":round(sum(ds)/len(ds),1),"product_count":len(ds)} for b,ds in bd.items()]
    res.sort(key=lambda x:x['avg_discount'], reverse=True)
    return res[:limit] if res else [{"brand":"No data","avg_discount":0,"product_count":0}]

def parse_changes():
    """Parse change_detection.json - has columns like issue_type, severity, old_rank, new_rank"""
    result = {
        "total_changes": len(change_data),
        "change_types": [],
        "keywords": [],
        "locations": [],
        "severity": {"Critical":0,"High":0,"Medium":0,"Low":0},
        "rank_drops": [],
        "rank_improvements": [],
        "new_entries": [],
        "disappeared": []
    }
    
    # Count by type
    type_counts = defaultdict(int)
    kw_counts = defaultdict(int)
    loc_counts = defaultdict(int)
    
    for row in change_data:
        issue = str(row.get('issue_type','')).strip()
        severity = str(row.get('severity','')).strip()
        keyword = str(row.get('keyword','')).strip()
        location = str(row.get('location','')).strip()
        product = str(row.get('product_name','')).strip()
        old_rank = str(row.get('old_rank',''))
        new_rank = str(row.get('new_rank',''))
        
        if issue: type_counts[issue] += 1
        if keyword: kw_counts[keyword] += 1
        if location: loc_counts[location] += 1
        
        # Severity
        if severity in result['severity']:
            result['severity'][severity] += 1
        
        # Rank drops
        try:
            old = int(old_rank) if old_rank.isdigit() else 0
            new = int(new_rank) if new_rank.isdigit() else 0
            if old > 0 and new > 0:
                if new > old:
                    result['rank_drops'].append({"product":product,"old_rank":old,"new_rank":new,"keyword":keyword,"location":location})
                elif new < old:
                    result['rank_improvements'].append({"product":product,"old_rank":old,"new_rank":new,"keyword":keyword,"location":location})
        except:
            pass
        
        # New entries (old_rank was 0 or empty)
        if old_rank in ['0','','-'] and new_rank not in ['0','','-','?']:
            result['new_entries'].append({"product":product,"new_rank":new_rank,"keyword":keyword,"location":location})
        
        # Disappeared
        if new_rank in ['0','','-'] and old_rank not in ['0','','-','?']:
            result['disappeared'].append({"product":product,"old_rank":old_rank,"keyword":keyword,"location":location})
    
    # Build change types
    for t, c in sorted(type_counts.items(), key=lambda x:x[1], reverse=True)[:10]:
        result['change_types'].append({"type":t,"count":c})
    
    for k, c in sorted(kw_counts.items(), key=lambda x:x[1], reverse=True)[:10]:
        result['keywords'].append({"keyword":k,"changes":c})
    
    for l, c in sorted(loc_counts.items(), key=lambda x:x[1], reverse=True)[:10]:
        result['locations'].append({"location":l,"changes":c})
    
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
    return {"status":"ok","version":"8.3","data_rows":len(main_data),"change_rows":len(change_data)}

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
    c = parse_changes()
    if c['total_changes']>0:
        return {"answer":f"Latest scan: {c['total_changes']:,} changes across {len(c['locations'])} locations. Top issue: {c['change_types'][0]['type'] if c['change_types'] else 'N/A'}. Check AI Insights tab for details."}
    return {"answer":"I'm Lilly! No recent scan data yet. Check the AI Insights tab or upload a CSV for diagnosis."}

if __name__=="__main__":
    import uvicorn; uvicorn.run(app,host="0.0.0.0",port=8000)