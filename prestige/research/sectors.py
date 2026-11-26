"""Point-in-time sector buckets for every stock that was in the Nifty100 between 2011 and 2026.
NSE-industry based, merged so most buckets have >= 3 live members in any year.
Buckets with < 3 live members on a date fall back to the EW universe for IR1."""
SECTORS = {
    "PVT_BANKS":    ["AXISBANK", "BANDHANBNK", "FEDERALBNK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK",
                     "YESBANK"],
    "PSU_BANKS":    ["BANKBARODA", "BANKINDIA", "CANBK", "IDBI", "IOB", "PNB", "SBIN", "UNIONBANK"],
    "NBFC":         ["ABCAPITAL", "BAJAJFINSV", "BAJAJHLDNG", "BAJFINANCE", "CHOLAFIN", "CHOLAHLDNG", "IFCI", "IRFC",
                     "JIOFIN", "LICHSGFIN", "LTF", "M&MFIN", "MUTHOOTFIN", "PEL", "PFC", "RECLTD", "SAMMAANCAP",
                     "SBICARD", "SHRIRAMFIN", "TATACAP"],
    "INS_CAPMKT":   ["BSE", "GICRE", "HDFCAMC", "HDFCLIFE", "ICICIGI", "ICICIPRULI", "LICI", "NIACL", "SBILIFE"],
    "IT":           ["HCLTECH", "INFY", "LTIM", "LTM", "MPHASIS", "OFSS", "TCS", "TECHM", "WIPRO"],
    "CONS_SERVICES": ["ETERNAL", "NYKAA", "PAYTM", "NAUKRI", "IRCTC", "INDHOTEL", "JUBLFOOD", "INDIGO", "SUNTV",
                      "ZEEL", "DMART", "TRENT"],
    "AUTO":         ["ASHOKLEY", "BAJAJ-AUTO", "BHARATFORG", "BOSCHLTD", "EICHERMOT", "EXIDEIND", "HEROMOTOCO",
                     "HYUNDAI", "M&M", "MARUTI", "MOTHERSON", "MRF", "TATAMOTORS", "TMCV", "TMPV", "TVSMOTOR"],
    "PHARMA_HC":    ["ABBOTINDIA", "ALKEM", "APOLLOHOSP", "AUROPHARMA", "BIOCON", "CIPLA", "DIVISLAB", "DRREDDY",
                     "GLAND", "GLAXO", "GLENMARK", "LUPIN", "MAXHEALTH", "SUNPHARMA", "TORNTPHARM", "ZYDUSLIFE"],
    "FMCG":         ["AWL", "BRITANNIA", "COLPAL", "DABUR", "EMAMILTD", "GODREJCP", "HINDUNILVR", "ITC", "MARICO",
                     "NESTLEIND", "PGHH", "TATACONSUM", "UBL", "UNITDSPR", "VBL"],
    "CONS_DURABLE": ["ASIANPAINT", "BERGEPAINT", "TITAN", "PAGEIND", "HAVELLS"],
    "CHEMICALS":    ["PIDILITIND", "PIIND", "SRF", "TATACHEM", "UPL"],
    "METALS_MIN":   ["COALINDIA", "HINDALCO", "HINDZINC", "JINDALSTEL", "JSWSTEEL", "NMDC", "SAIL", "TATASTEEL",
                     "VEDL", "VAML"],
    "OIL_GAS":      ["ATGL", "BPCL", "CASTROLIND", "GAIL", "HINDPETRO", "IGL", "IOC", "MRPL", "OIL", "ONGC",
                     "PETRONET", "RELIANCE"],
    "POWER":        ["ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "NHPC", "NTPC", "POWERGRID", "RPOWER", "TATAPOWER",
                     "TORNTPOWER"],
    "CAPGOODS_DEF": ["ABB", "BHEL", "CGPOWER", "CUMMINSIND", "ENRIN", "LT", "POLYCAB", "POWERINDIA", "SIEMENS",
                     "SUZLON", "PUNJLLOYD", "BEL", "HAL", "MAZDOCK", "SOLARINDS"],
    "CEMENT":       ["ACC", "AMBUJACEM", "GRASIM", "SHREECEM", "ULTRACEMCO"],
    "INFRA_REALTY": ["ADANIENT", "ADANIPORTS", "CONCOR", "GMRAIRPORT", "JPASSOCIAT", "RELINFRA", "DLF", "HDIL",
                     "UNITECH", "EMBDL"],
    "TELECOM":      ["BHARTIARTL", "IDEA", "RCOM", "TTML", "INDUSTOWER"],
}
SYMBOL_TO_SECTOR = {s: k for k, v in SECTORS.items() for s in v}
CODE_MAP = {"500180": "HDFCBANK", "524715": "SUNPHARMA", "532461": "PNB", "532810": "PFC", "532822": "IDEA",
            "500480": "CUMMINSIND", "543187": "POWERINDIA"}
