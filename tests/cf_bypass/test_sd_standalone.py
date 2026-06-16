"""Test cf_sciencedirect_full.py standalone."""
import sys, asyncio, importlib.util
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Load module
spec = importlib.util.spec_from_file_location(
    "cf_sd", r"D:\dogtor\甲醇转多元醇的生物合成\构建好的脚本\cf_sciencedirect_full.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["cf_sd"] = mod
spec.loader.exec_module(mod)

# Run with test URL
sys.argv = ["test", "https://www.sciencedirect.com/science/article/pii/S002195172300001X"]
asyncio.run(mod.main())
