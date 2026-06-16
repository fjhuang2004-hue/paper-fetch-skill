"""Test Unpaywall API for OA status lookup. Usage: python test_unpaywall.py <DOI> [DOI...]"""
import sys, json, urllib.request, urllib.error

EMAIL = "your-email@example.com"  # Unpaywall asks for email, replace as needed

def check_doi(doi):
    url = f"https://api.unpaywall.org/v2/{doi.strip()}?email={EMAIL}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data
    except urllib.error.HTTPError as e:
        return {"doi": doi, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"doi": doi, "error": str(e)}

def show(data):
    doi = data.get("doi", "N/A")
    if "error" in data:
        print(f"{doi}: ERROR {data['error']}")
        return
    oa = data.get("is_oa", "unknown")
    status = data.get("oa_status", "unknown")
    loc = data.get("best_oa_location") or {}
    print(f"{doi}")
    print(f"  is_oa: {oa}  |  oa_status: {status}")
    if loc:
        print(f"  best_oa_url:     {loc.get('url', 'N/A')[:120]}")
        print(f"  host_type:       {loc.get('host_type', 'N/A')}")
        print(f"  license:         {loc.get('license', 'N/A')}")
        print(f"  version:         {loc.get('version', 'N/A')}")
    # Also list all oa_locations
    all_locs = data.get("oa_locations", [])
    if len(all_locs) > 1:
        print(f"  all oa locations ({len(all_locs)}):")
        for l in all_locs[1:]:
            print(f"    - {l.get('host_type', '?')}: {l.get('url', 'N/A')[:100]}")
    print()

for doi in sys.argv[1:]:
    data = check_doi(doi)
    show(data)
