"""Wrapper to test cf_unified.py with UTF-8 stdout."""
import sys, asyncio
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Load the module
import importlib.util
spec = importlib.util.spec_from_file_location(
    "cf_unified",
    r"D:\dogtor\甲醇转多元醇的生物合成\构建好的脚本\cf_unified.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules["cf_unified"] = mod

# Set sys.argv before exec so main() can read it
orig_argv = sys.argv
sys.argv = [
    r"D:\dogtor\甲醇转多元醇的生物合成\构建好的脚本\cf_unified.py",
    orig_argv[1] if len(orig_argv) > 1 else "https://pubs.acs.org/doi/10.1021/acscatal.2c04683"
]

spec.loader.exec_module(mod)

# Run main
asyncio.run(mod.main())
