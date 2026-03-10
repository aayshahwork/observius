"""Data extraction example — from the README "Data Extraction" section.

Run:
    python examples/example_data_extraction.py
"""

from computeruse import ComputerUse

cu = ComputerUse()

result = cu.run_task(
    url="https://finance.yahoo.com/quote/AAPL",
    task="Get the current stock price and today's change percentage",
    output_schema={
        "price": "float",
        "change_pct": "float",
        "currency": "str",
    },
)

data = result.result
print(f"AAPL: {data['currency']}{data['price']}  ({data['change_pct']:+.2f}%)")
