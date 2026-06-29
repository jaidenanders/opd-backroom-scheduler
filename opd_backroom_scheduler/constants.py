import os

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
DAYS        = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL    = {"Mon":"Monday","Tue":"Tuesday","Wed":"Wednesday","Thu":"Thursday",
               "Fri":"Friday","Sat":"Saturday","Sun":"Sunday"}
SHIFT_SUGGESTIONS = ["5-2", "6-3", "7-4", "9-6", "11-8", "1-10", ""]

CLR_HEADER_BLUE  = "#1565C0"
CLR_BLUE_LIGHT   = "#BBDEFB"
CLR_GOLD         = "#FFD54F"
CLR_GOLD_DARK    = "#F9A825"
CLR_BG           = "#F5F5F5"
CLR_WHITE        = "#FFFFFF"
CLR_DARK         = "#1A1F2E"
CLR_MUTED        = "#9AA0B4"
CLR_PERSONAL     = "#E3F2FD"
CLR_BACKROOM     = "#FFF8E1"
CLR_GRAY_ROW     = "#D6D6D6"
CLR_GRAY_TEXT    = "#9E9E9E"
CLR_BACKROOM_BTN = "#1565C0"
CLR_PERSONAL_BTN = "#2E7D32"

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backroom_scheduler.db")

# ── COVERAGE THRESHOLDS ───────────────────────────────────────────────────────
# Keys are minimum headcount values; the entry applies when n <= next_key - 1
# Stored as a plain list of (min_count, color) sorted ascending.
# Default: red≤1, orange≤2, yellow≤3, light-green≤4, dark-green=5+
DEFAULT_THRESHOLDS = [
    (0, "#EF5350"),   # red
    (1, "#FF9800"),   # orange
    (2, "#FFF176"),   # yellow
    (3, "#A5D6A7"),   # light green
    (4, "#66BB6A"),   # medium green
    (5, "#2E7D32"),   # dark green  (5+ = great)
]
# Runtime thresholds — modified by settings dialog
_thresholds = [list(t) for t in DEFAULT_THRESHOLDS]

# Backroom capacity cap — modified by settings dialog
_backroom_max = 10
