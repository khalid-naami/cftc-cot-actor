"""
Market asset definitions and CFTC Contract Market Codes.
"""

# Format: [Asset Name, CFTC Contract Code, CFTC Report ID, Category]
CHICAGO = [
    ['EUR', '099741', 'deacmesf', 'forex'],
    ['JPY', '097741', 'deacmesf', 'forex'],
    ['AUD', '232741', 'deacmesf', 'forex'],
    ['NZD', '112741', 'deacmesf', 'forex'],
    ['CAD', '090741', 'deacmesf', 'forex'],
    ['GBP', '096742', 'deacmesf', 'forex'],
    ['CHF', '092741', 'deacmesf', 'forex'],
    ['MXN', '095741', 'deacmesf', 'forex'],
    ['BRL', '102741', 'deacmesf', 'forex'],
    ['ZAR', '122741', 'deacmesf', 'forex'],
    ['BTC', '133741', 'deacmesf', 'crypto'],
    ['ETH', '146021', 'deacmesf', 'crypto'],
    ['NASDAQ-100', '209742', 'deacmesf', 'index'],
    ['S&P 500', '13874A', 'deacmesf', 'index'],
]

DJ = [['DOW JONES', '124603', 'deacbtsf', 'index']]
USD = [['USD', '098662', 'deanybtsf', 'forex']]
NEW_YORK = [
    ['OIL', '067651', 'deanymesf', 'other'],
    ['GAS', '023651', 'deanymesf', 'other'],
]
COMMODITY = [
    ['SILVER', '084691', 'deacmxsf', 'metals'],
    ['COPPER', '085692', 'deacmxsf', 'metals'],
    ['GOLD', '088691', 'deacmxsf', 'metals'],
]

ALL_ASSETS = CHICAGO + DJ + USD + NEW_YORK + COMMODITY
ASSET_NAMES = [a[0] for a in ALL_ASSETS]
ASSET_MAP = {a[0]: {"code": a[1], "report": a[2], "category": a[3]} for a in ALL_ASSETS}
