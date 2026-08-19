import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

GOOGLE_SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


def get_google_client(creds_json):
    if not creds_json:
        logger.warning("Credentials JSON is empty. Cannot create Google client.")
        return None
    try:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, GOOGLE_SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Failed to authorize Google Client: {e}")
        return None
