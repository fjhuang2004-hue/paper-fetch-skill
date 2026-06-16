"""Batch test cf_unified.py — all 8 publishers × 3 repetitions."""
import sys, os, asyncio, json, time, importlib.util

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Load cf_unified module
orig_argv = sys.argv
spec = importlib.util.spec_from_file_location(
    "cf_unified",
    r"D:\dogtor\甲醇转多元醇的生物合成\构建好的脚本\cf_unified.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["cf_unified"] = mod
spec.loader.exec_module(mod)

# Test URLs for each publisher
PUBLISHERS = {
    "ACS":          "https://pubs.acs.org/doi/10.1021/acscatal.2c04683",
    "Wiley":        "https://onlinelibrary.wiley.com/doi/10.1002/anie.202300001",
    "ScienceDirect":"https://www.sciencedirect.com/science/article/pii/S002195172300001X",
    "PNAS":         "https://www.pnas.org/doi/10.1073/pnas.2300001",
    "ASM":          "https://journals.asm.org/doi/10.1128/jb.00001-23",
    "OUP":          "https://academic.oup.com/nar/article/51/1/1/7000001",
    "TandF":        "https://www.tandfonline.com/doi/full/10.1080/15476286.2023.0000001",
    "cell.com":     "https://www.cell.com/cell/fulltext/S0092-8674(23)00001-X",
}

REPETITIONS = 3

async def main():
    global sys
    results = {}
    total_pass = 0
    total_fail = 0

    for name, url in PUBLISHERS.items():
        pub_results = []
        for r in range(1, REPETITIONS + 1):
            t0 = time.time()
            sys.argv = ["cf_unified.py", url]
            try:
                result = await mod.try_once(url)
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            elapsed = time.time() - t0

            passed = result.get("ok", False)
            pub_results.append({
                "round": r,
                "ok": passed,
                "cf_type": result.get("cf_type", "?"),
                "title": str(result.get("title", ""))[:80],
                "error": result.get("error", ""),
                "elapsed": round(elapsed, 1),
            })
            status = "PASS" if passed else "FAIL"
            print(f"  {status} {name} [{r}/{REPETITIONS}] cf_type={result.get('cf_type','?')} {elapsed:.1f}s")

            if passed:
                total_pass += 1
            else:
                total_fail += 1

            if r < REPETITIONS:
                await asyncio.sleep(1)

        results[name] = pub_results
        passed_count = sum(1 for r in pub_results if r["ok"])
        print(f"  => {name}: {passed_count}/{REPETITIONS} passed")
        print()

    print("=" * 60)
    print(f"TOTAL: {total_pass}/{total_pass+total_fail} passed ({total_pass+total_fail} tests)")
    for name, pub_results in results.items():
        passed_count = sum(1 for r in pub_results if r["ok"])
        if passed_count == REPETITIONS:
            ok_str = "OK"
        elif passed_count > 0:
            ok_str = "PARTIAL"
        else:
            ok_str = "FAIL"
        details = " | ".join(f"{'PASS' if r['ok'] else 'FAIL'} {r['cf_type']}" for r in pub_results)
        print(f"  {ok_str:8s} {name}: {passed_count}/{REPETITIONS}  [{details}]")

asyncio.run(main())
