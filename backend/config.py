import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
BACKEND_DIR = BASE_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "equity_research.db"

EXISTING_DATA_DIR = Path("/home/ubuntu/FinEng/BseIndiaApi/src/examples/Bse_Nse_announcement_downloads")

MAX_MARKET_CAP = 10000  # 10,000 Cr - small cap / micro cap focus

# Curated small cap / micro cap stocks (market cap < 10,000 Cr)
SECTORS = {
    "banking_finance": [
        "EQUITASBNK", "UJJIVANSFB", "DCBBANK", "KARURVYSYA", "TMBANK",
        "SOUTHBANK", "INDIANB", "UCOBANK", "MAHABANK", "CENTRALBK",
        "FEDERALBNK", "BANDHANBNK", "IDBI", "PNB", "CANBK",
        "IIFL", "MOTILALOFS", "ANGELONE", "CDSL", "KFINTECH",
        "CAMS", "BSE", "MCX", "INDIAGRID", "INDIGOPNTS"
    ],
    "it_software": [
        "KPITTECH", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS",
        "TATAELXSI", "HAPPSTMNDS", "ZENTEC", "ROUTE", "SONATSOFTW",
        "BIRLASOFT", "POLYCAB", "DATAPATTNS", "ZAGGLE", "CIGNITITEC",
        "TANLA", "INFOBIP", "KELLTONTEC", "THIRUSUFI", "MPSLTD",
        "BSOFT", "RAILTEL", "TATAELXSI", "IRCTC", "IRFC"
    ],
    "pharma": [
        "LAURUSLABS", "GRANULES", "NATCOPHARM", "IPCALAB", "ALKEM",
        "TORNTPHARM", "ZYDUSLIFE", "GLENMARK", "AUROPHARMA", "LUPIN",
        "ALKYLAMINE", "SUPRIYA", "BLISSGVS", "HEMIPROP", "MEDPLUS",
        "KOPRAN", "LAXMIPATI", "GRASIM", "APOLLOHOSP", "METROPOLIS",
        "LALPATHLAB", "DRREDDY", "CIPLA", "SUNPHARMA", "DIVISLAB"
    ],
    "chemicals": [
        "DEEPAKNTR", "ATUL", "NAVINFLUOR", "FLUOROCHEM", "PIIND",
        "SRF", "CLEAN", "TATVA", "ALKYLAMINE", "ANURAS",
        "BODALCHEM", "GALAXYSURF", "COROMANDEL", "UPL", "BASF",
        "BAYERCROP", "GNFC", "GSFC", "NFL", "RCF",
        "DEEPAKNTR", "NAVINFLUOR", "FLUOROCHEM", "ATUL", "CLEAN"
    ],
    "auto_ancillary": [
        "SONACOMS", "MINDAIND", "BALKRISIND", "TVSSRICHAKRA", "SUPRAJIT",
        "JAMNAAUTO", "VARROC", "SCHAEFFLER", "SKFINDIA", "TIMKEN",
        "GREAVESCOT", "EXIDEIND", "AMARAJABAT", "HBLPOWER", "TATAELXSI",
        "MOTHERSON", "BOSCHLTD", "MOTHERSON", "SONACOMS", "MINDAIND"
    ],
    "textiles": [
        "KPRMILL", "VARDHMAN", "WELSPUNIND", "TRIDENT", "ALOKINDS",
        "SPAL", "RSWM", "NAHARSPIN", "VIPCLOTHI", "LAKSHMILLY",
        "RAYMOND", "CENTURYTEX", "NCC", "GRASIM", "PAGEIND",
        "TATACONSUM", "ITC", "HINDUNILVR", "DABUR", "MARICO"
    ],
    "energy_infra": [
        "SJVN", "NHPC", "NLCINDIA", "TATAPOWER", "ADANIGREEN",
        "JSWENERGY", "TORNTPOWER", "CESC", "JPPOWER", "NLCINDIA",
        "RECLTD", "PFC", "IRFC", "RVNL", "RAILTEL",
        "ITI", "HAL", "BEL", "BDL", "COCHINSHIP"
    ],
    "metals_mining": [
        "NATIONALUM", "NMDC", "SAIL", "JINDALSTEL", "JSWSTEEL",
        "VEDL", "HINDALCO", "COALINDIA", "MOIL", "KIOCL",
        "RATNAMANI", "APLAPOLLO", "WELCORP", "JSLHISAR", "MAHSEAMLES",
        "TATASTEEL", "HINDZINC", "JINDALSTEL", "SAIL", "NMDC"
    ],
    "realty": [
        "OBEROIRLTY", "BRIGADE", "SOBHA", "PRESTIGE", "GODREJPROP",
        "DLF", "PHOENIXLTD", "KALYANKJIL", "LODHA", "MAHLIFE",
        "SUNTECK", "NAVNEET", "MAHLOG", "TATACONSUM", "EMAMILTD",
        "RADICO", "UBL", "GODREJIND", "EIDPARRY", "KALYANKJIL"
    ],
    "consumer": [
        "EMAMILTD", "RADICO", "VGUARD", "SYMPHONY", "BATAINDIA",
        "RELAXO", "CROMPTON", "VOLTAS", "BLUESTARLT", "HAVELLS",
        "ORIENTELEC", "POLYCAB", "CDSL", "BSE", "CAMS",
        "KFINTECH", "IIFL", "MOTILALOFS", "ANGELONE", "MCX"
    ],
    "healthcare": [
        "METROPOLIS", "LALPATHLAB", "MAXHEALTH", "APOLLOHOSP", "FORTISHEALTH",
        "NARAYANA", "ASTER", "RAINBOW", "MEDANTA", "GRANULES",
        "LAURUSLABS", "NATCOPHARM", "SYNGENE", "BIOCON", "PFIZER",
        "GLENMARK", "ALKEM", "TORNTPHARM", "IPCALAB", "DRREDDY"
    ],
    "defence_aerospace": [
        "HAL", "BEL", "BDL", "COCHINSHIP", "GRSE",
        "MAZAGON", "DATAPATTNS", "PARAS", "DYNAMATECH", "SOLARINDS",
        "A2ZMESSENGERS", "AFFLE", "TANLA", "ZENTEC", "ROUTE",
        "KPITTECH", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"
    ]
}

IMPORTANT_CATEGORIES = [
    "Financial Results", "Board Meeting", "Dividend", "Bonus",
    "Investor Presentation", "Earnings Call Transcript",
    "Award of Order / Receipt of Order", "Awarding of order(s)/contract(s)",
    "Bagging/Receiving of orders/contracts", "Capacity addition",
    "Commencement of commercial production/operations",
    "Press Release", "Press Release / Media Release",
    "Analysts/Institutional Investor Meet/Con. Call Updates",
    "Preferential Issue", "New Listing", "Board Meeting Rescheduled",
    "Outcome of Board Meeting", "Credit Rating", "Credit Rating- New",
    "Credit Rating- Revision"
]

EXCLUDED_CATEGORIES = [
    "Newspaper Publication", "Resignation of Independent director",
    "Corrigendum", "Postal Ballot", "Change in Director(s)",
    "Copy of Newspaper Publication", "Change in Directorate",
    "Appointment", "Cessation", "Change in Registered Office Address",
    "Change in Auditors", "Retirement",
    "Change in Company Secretary/Compliance Officer",
    "Certificate under SEBI (Depositories and Participants) Regulations, 2018",
    "Appointment of Statutory Auditor/s", "Address Change",
    "Compliance Report", "Others", "General Updates",
    "Change in Management", "Structural Digital Database",
    "Resignation of Director",
    "Reg. 39 (3) - Details of Loss of Certificate / Duplicate Certificate",
    "Resignation of Statutory Auditors", "Closure of Trading Window",
    "Clarification",
    "Certificate under Reg. 74 (5) of SEBI (DP) Regulations, 2018",
]
