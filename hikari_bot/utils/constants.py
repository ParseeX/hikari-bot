import os

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
DECK_DIR = os.path.join(os.path.dirname(ROOT_DIR), "deck")
PDF_DIR = os.path.join(os.path.dirname(ROOT_DIR), "pdf")
DATA_DIR = os.path.join(os.path.dirname(ROOT_DIR), "data")
RESOURCES_DIR = os.path.join(os.path.dirname(ROOT_DIR), "resources")
WEB_DIR = os.path.join(os.path.dirname(ROOT_DIR), "plugins/web")

IMAGE_ORIGIN = "https://images.ygoprodeck.com/images/cards_cropped/"
IMAGE_CHINESE = "https://cdn.233.momobako.com/ygopro/pics/"
CARD_SEARCH = "https://ygocdb.com/api/v0/?search="
FAQ = "https://ygocdb.com/faq/"
YGOPRO = "C:/Users/xu_yi/AppData/Roaming/MyCardLibrary/ygopro"

WINDOENT_BASE_API = "https://yugiohmatchapi.windoent.com/"
API_MATCH_SEARCH = "v1/match"
API_MATCH_INFO = "v1/match/info/"

JIHUANSHE_BASE_API = "https://api.jihuanshe.com/api/"
API_NEW_TOURNAMENT = "tournaments?token="
API_TOURNAMENT = "tournaments/{id}?tournament_code={code}&token="
API_CONTESTANTS = "contestants?tournament_id={id}&page={page}&token="
API_CHECK_IN = "contestants/verify?token="
API_QUIT = "contestants/quit?token="
API_PAIRING = "battles/all?tournament_id={id}&round={round}&token="
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOjc4MDI2NSwiaXNzIjoiaHR0cDovL2FwaS5qaWh1YW5zaGUuY29tL2FwaS93ZWNoYXQvbG9naW4iLCJpYXQiOjE3NTA2NjQwODIsImV4cCI6MTc1NTg0ODA4MiwibmJmIjoxNzUwNjY0MDgyLCJqdGkiOiIzVXVzaERKTWw2clhaT1RwIn0.IX0E-qKOqKF2l9Me7NT6VomTR66erms1651qW7KC-xQ"

MC_BASE_API = "https://sapi.moecube.com:444/ygopro/"
API_PLAYER_HISTORY = "arena/history"
API_PLAYER_INFO = "arena/user"
API_PLAYER_HISTORY_RANK = "arena/historyScore"
API_FIRST_WIN = "arena/firstwin"

YGOCDB = os.path.join(DATA_DIR, 'card_info.db')
MOECARD_DB = os.path.join(DATA_DIR, 'card.cdb')
CARD_PICS = os.path.join(DATA_DIR, 'pics')

JM_DIR = os.path.join(DATA_DIR, "jm")

WS_URL = "wss://tiramisu.moecube.com:8923/?filter=started"