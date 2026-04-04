"""Run all E2E tests in sequence"""
import subprocess
import sys
import time

tests = [
    ("Test 1: Enrichment + Verification", "e2e_tests/test_1_enrichment.py"),
    ("Test 2: Cost Tracking (AI)", "e2e_tests/test_2_cost_tracking.py"),
    ("Test 3: Budget Circuit Breaker", "e2e_tests/test_3_budget_breaker.py"),
    ("Test 4: Post-Action Verification", "e2e_tests/test_4_verification.py"),
    ("Test 5: Full Pipeline (explore→compile→replay)", "e2e_tests/test_5_full_pipeline.py"),
    ("Test 6: Click Chain (Wikipedia)", "e2e_tests/test_6_click_chain.py"),
    ("Test 7: Interrupt Safety", "e2e_tests/test_7_interrupt_safety.py"),
]

print("=" * 60)
print("  POKANT E2E FEATURE VALIDATION")
print("  Running all tests against real websites")
print("=" * 60)

start = time.time()
results = []

for name, path in tests:
    print(f"\n{'─' * 60}")
    print(f"  Running: {name}")
    print(f"{'─' * 60}")
    
    result = subprocess.run([sys.executable, path], cwd="..")
    passed = result.returncode == 0
    results.append((name, passed))
    
    if not passed:
        print(f"\n  ⚠️  {name} exited with code {result.returncode}")

elapsed = time.time() - start

print(f"\n{'=' * 60}")
print(f"  SUMMARY ({elapsed:.0f}s total)")
print(f"{'=' * 60}")
for name, passed in results:
    print(f"  {'✅' if passed else '❌'} {name}")

passed_count = sum(1 for _, p in results if p)
print(f"\n  {passed_count}/{len(results)} tests passed")

print(f"\n  Check localhost:3000 for these runs:")
print(f"    • e2e-enrichment-test")
print(f"    • e2e-cost-test")
print(f"    • e2e-budget-test")
print(f"    • e2e-pipeline")
print(f"    • e2e-click-chain")
print(f"    • e2e-interrupt-test")
print(f"\n  Cleanup: rm -rf .pokant e2e_tests")
