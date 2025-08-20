import os
import json
from hikari_bot.utils.constants import *

whitelist_file = os.path.join(DATA_DIR, 'whitelist.json')

def get_whitelist():
    try:
        with open(whitelist_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"groups": [], "users": []}
    except json.JSONDecodeError:
        return {"groups": [], "users": []}
    
def save_whitelist(whitelist):
    with open(whitelist_file, 'w', encoding='utf-8') as f:
        json.dump(whitelist, f, indent=4, ensure_ascii=False)

def add_group_to_whitelist(group_id):
    whitelist = get_whitelist()
    if group_id not in whitelist["groups"]:
        whitelist["groups"].append(group_id)
        save_whitelist(whitelist)
        return True
    return False

def is_allowed_group(group_id) -> bool:
    return group_id in get_whitelist()["groups"]

