import azure.functions as func
import requests
import logging
import json
import re
from typing import Dict, Any

def extract_notification_email(body_preview: str) -> Dict[str, Any]:
    """
    Extracts notification details from email body preview.
    Improved version with consistent value extraction using regex and consolidated logic.
    """
    lines = body_preview.split("\r\n\r\n\r\n")[1].split()
    if len(lines) < 2:
        return {'process': 'unknown', 'error': 'Invalid body preview format'}

    process = lines[1]
    info = {'process': process}

    # Extract value using regex for consistency
    amount_match = re.search(r'\$([0-9.,]+)', body_preview)
    if amount_match:
        value_str = amount_match.group(1).replace('.', '').replace(',', '')
        info['value'] = value_str

    # Extract other fields based on process type
    if process == 'Compraste':
        idx_wh = 0
        whe = ''
        for idx, val in enumerate(lines):
            if idx == 3:
                idx_wh += 1
            elif idx_wh == 1 and val.lower() == 'con':
                info['where'] = whe.strip()
                whe = ''
                idx_wh += 1
            elif idx_wh == 1:
                whe += val + " "
            elif idx_wh == 2 and val.lower() == 'tu':
                idx_wh += 1
            elif idx_wh == 3 and val.lower().strip() != 'el':
                whe += val + " "
            elif idx_wh == 3 and val.lower().strip() == 'el':
                info['with'] = whe.strip().replace(',', '')
                whe = ''
                idx_wh += 1
            elif idx_wh == 4:
                info['date'] = val
                break

    elif process == 'Transferiste':
        if len(lines) > 12:
            info['from'] = f"{lines[5]} {lines[6]}"
            info['to'] = f"{lines[9]} {lines[10]}"
            info['date'] = lines[12]

    elif process == 'Pagaste':
        idx_wh = 0
        whe = ''
        for idx, line in enumerate(lines[2:], 2):
            if idx >= 4 and line != 'desde' and idx_wh == 0:
                whe += line + " "
            elif idx >= 4 and line == 'desde':
                info['to'] = whe.strip()
                whe = ''
                idx_wh = 1
            elif line.lower() == 'producto' and idx_wh == 1:
                idx_wh = 2
            elif idx_wh == 2:
                info['with'] = line.strip()
                idx_wh = 3
            elif line.lower() == 'el' and idx_wh == 3:
                idx_wh = 4
            elif idx_wh == 4:
                info['date'] = line
                break

    elif process == 'Recibiste':
        idx_wh = 0
        whe = ''
        for idx, line in enumerate(lines[5:], 5):
            if idx >= 7 and line != 'en' and idx_wh == 0:
                whe += line + " "
            elif idx >= 7 and line == 'en':
                info['from'] = whe.strip()
                whe = ''
                idx_wh = 1
            elif line.lower() == 'tu' and idx_wh == 1:
                idx_wh = 2
            elif idx_wh == 2:
                if line.lower() == 'el':
                    info['with'] = whe.strip()
                    whe = ''
                    idx_wh = 3
                else:
                    whe += line + " "
            elif idx_wh == 3:
                info['date'] = line
                break

    elif process == 'Retiraste':
        idx_wh = 0
        whe = ''
        for idx, line in enumerate(lines[2:], 2):
            if idx >= 4 and line != 'de' and idx_wh == 0:
                whe += line + " "
            elif idx >= 4 and line == 'de':
                info['where'] = whe.strip()
                whe = ''
                idx_wh = 1
            elif idx_wh == 1 and line.lower() == 'tu':
                idx_wh = 2
            elif idx_wh == 2:
                if line.lower() == 'el':
                    info['with'] = whe.strip()
                    whe = ''
                    idx_wh = 3
                else:
                    whe += line + " "
            elif idx_wh == 3:
                info['date'] = line
                break

    return info

