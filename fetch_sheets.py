import requests, csv, io, json, os

URLS = {
    "main": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR8QstFSCz7VvTOK_zcJiuc09__bM0skQ61Ms0U4yjwytSgp_3-nR6wPM3uBGjJfmvA4BuUXBBnrzJG/pub?gid=1772596335&single=true&output=csv",
    "change_detection": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR8QstFSCz7VvTOK_zcJiuc09__bM0skQ61Ms0U4yjwytSgp_3-nR6wPM3uBGjJfmvA4BuUXBBnrzJG/pub?gid=1807550478&single=true&output=csv",
    "ai_insight": "https://docs.google.com/spreadsheets/d/e/2PACX-1vR8QstFSCz7VvTOK_zcJiuc09__bM0skQ61Ms0U4yjwytSgp_3-nR6wPM3uBGjJfmvA4BuUXBBnrzJG/pub?gid=1066248165&single=true&output=csv"
}

def fetch_csv(url):
    return list(csv.DictReader(io.StringIO(requests.get(url, timeout=30).text)))

def save_json(name, data):
    os.makedirs("data", exist_ok=True)
    with open(f"data/{name}.json", "w") as f: json.dump(data, f)

if __name__ == "__main__":
    for name, url in URLS.items():
        try:
            save_json(name, fetch_csv(url))
            print(f"✅ {name} saved")
        except Exception as e:
            print(f"❌ {name}: {e}")