import azure.functions as func
import logging
from typing import Dict, Optional, Any, List
from bs4 import BeautifulSoup
import requests
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants for repeated strings
APPROVED_STATE = "Aprobado"
UNKNOWN_STATE = "Desconocido"
ERROR_MSG = "[ERROR] No se pudo obtener el contenido del correo"

# Standard keys for payment data
PAYMENT_KEYS = ['value', 'to', 'date', 'cus', 'state']

def _safe_int(value: str) -> Optional[int]:
    """Safely convert string to int, handling common formats."""
    if not value:
        return None
    try:
        # Remove common separators and currency symbols
        cleaned = value.replace(",", "").replace(".", "").replace("$", "").strip()
        return int(cleaned)
    except ValueError:
        logger.warning(f"Could not convert '{value}' to int")
        return None

def _extract_from_tags(soup: BeautifulSoup, tag_name: str, patterns: Dict[str, str]) -> Dict[str, Any]:
    """Helper to extract values from tags based on patterns."""
    result = {key: None for key in PAYMENT_KEYS}
    try:
        tags = soup.find_all(tag_name)
        for tag in tags:
            text = tag.get_text(strip=True)
            for key, pattern in patterns.items():
                if pattern in text:
                    if key == 'value':
                        result[key] = _safe_int(text.split(":")[-1].strip())
                    else:
                        result[key] = text.split(":")[-1].strip()
                    break
    except Exception as e:
        logger.error(f"Error extracting from {tag_name} tags: {e}")
    return result

def extract_from_alkosto(soup: BeautifulSoup, to: str) -> Dict[str, Any]:
    """
    Extract payment data from Alkosto HTML.

    Args:
        soup: Parsed HTML content.
        to: Recipient information.

    Returns:
        Dict with payment data.
    """
    values = {}
    try:
        tags = soup.find_all("td")
        key = ''
        idx_cus = 0
        idx_date = 0
        for tag in tags:
            text = tag.get_text(strip=True)
            if key == 'value':
                values['value'] = _safe_int(text)
                key = ''
                continue
            elif 'Resumen de tu pedido' in text and idx_cus == 1:
                values['cus'] = text.split(" ")[-2]
                idx_cus = 0
            elif 'Resumen de tu pedido' in text:
                idx_cus = 1
            elif text == 'Total: ' and 'value' not in values:
                key = 'value'
            elif idx_date == 4:
                values['date'] = text.split(" ")[0]
                idx_date = 0
            elif 'Fecha de entrega' in text:
                idx_date += 1

        if len(values) >= 3:
            values['to'] = to
            values['state'] = APPROVED_STATE
    except Exception as e:
        logger.error(f"Error in extract_from_alkosto: {e}")
    return values

def extract_invoice_vue(soup: BeautifulSoup, to: str) -> Dict[str, Any]:
    """
    Extract payment data from Vue invoice HTML.

    Args:
        soup: Parsed HTML content.
        to: Recipient information.

    Returns:
        Dict with payment data.
    """
    values = {}
    try:
        tags = soup.find_all("td")
        keys = ['Total', 'Invoice Number:', 'Transaction Date:']
        key = ''
        for tag in tags:
            text = tag.get_text(strip=True).replace("\u200e", "")
            if key:
                values[key] = text
                key = ''
                continue
            elif text in keys:
                key = text
            elif text == 'USD':
                values['money'] = text

        if len(values) >= 4:
            values['cus'] = values.pop('Invoice Number:')
            values['value'] = _safe_int(values.pop('Total'))
            values['date'] = values.pop('Transaction Date:')
            values['to'] = to
            values['state'] = APPROVED_STATE
    except Exception as e:
        logger.error(f"Error in extract_invoice_vue: {e}")
    return values

def extract_enel_invoice(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from Enel invoice HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    patterns = {
        'date': 'Fecha del pago',
        'value': 'Valor',
        'to': 'Enel',
        'cus': 'Factura'
    }
    result = _extract_from_tags(soup, "td", patterns)
    if result.get('to'):
        result['state'] = APPROVED_STATE
    return result

def extract_claro_invoice(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from Claro invoice HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    patterns = {
        'date': 'Fecha del pago',
        'value': 'Valor',
        'to': 'Claro',
        'cus': 'Referente de pago'
    }
    result = _extract_from_tags(soup, "td", patterns)
    if result.get('to'):
        result['state'] = APPROVED_STATE
    return result

def extract_payment_gas(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from gas payment HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    values = {}
    try:
        tags = soup.find_all("span")
        keys = ["Valor", "Convenio", "Fecha", "ID transacción"]
        key = ''
        for tag in tags:
            text = tag.get_text(strip=True)
            if key:
                values[key] = text
                key = ''
                continue
            if text in keys:
                key = text
        if len(values) == 4:
            values['cus'] = values.pop('ID transacción')
            values['to'] = values.pop('Convenio')
            values['value'] = _safe_int(values.pop('Valor'))
            values['date'] = values.pop('Fecha')
            values['state'] = APPROVED_STATE
    except Exception as e:
        logger.error(f"Error in extract_payment_gas: {e}")
    return values

def extract_basic_info(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract basic state information from HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        State string or None.
    """
    try:
        possible_tags = [soup.find(tag) for tag in ['strong', 'h2', 'p', 'div', 'span']]
        state = next((tag.get_text(strip=True) for tag in possible_tags if tag and tag.string), None)
        return state
    except Exception as e:
        logger.error(f"Error in extract_basic_info: {e}")
        return None

def extract_payment_values(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment values with flexibility.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with extracted values.
    """
    fields = {
        'cus': ['CUS:', 'Número CUS:', 'Código CUS'],
        'to': ['Empresa:', 'Comercio:', 'Destino:'],
        'value': ['Valor de la Transacción:', 'Monto:', 'Importe:'],
        'date': ['Fecha de Transacción:', 'Fecha:', 'Día de operación:']
    }

    extracted = {key: None for key in fields}
    try:
        for key, phrases in fields.items():
            for phrase in phrases:
                tag = soup.find(string=lambda t: t and phrase in t)
                if tag:
                    value = tag.strip().split(':')[-1].strip()
                    if len(value) > 1:
                        if key == 'value':
                            extracted[key] = _safe_int(value)
                        else:
                            extracted[key] = value
                        break
        if any(extracted.values()):
            extracted['state'] = APPROVED_STATE
    except Exception as e:
        logger.error(f"Error in extract_payment_values: {e}")
    return extracted

def extract_from_notification(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from notification HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    payment_data = {key: None for key in PAYMENT_KEYS}
    try:
        value = _safe_int(soup.find("b", string="Valor: ").next_sibling.strip().replace(",", ""))
        to = soup.find("b", string="Concepto: ").next_sibling.strip()
        date = soup.find("b", string="Fecha transacción: ").next_sibling.strip()
        cus = soup.find("b", string="Número de aprobación: ").next_sibling.strip()
        state = APPROVED_STATE if soup.find_all("b", string="EXITOSO") else None

        payment_data.update({
            "value": value,
            "to": to,
            "date": date,
            "cus": cus,
            "state": state
        })
    except AttributeError as e:
        logger.warning(f"AttributeError in extract_from_notification: {e}")
    except Exception as e:
        logger.error(f"Error in extract_from_notification: {e}")
    return payment_data

def get_payment_data(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from standard spans.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    payment_data = {key: None for key in PAYMENT_KEYS}
    try:
        value = _safe_int(soup.find("span", string="Valor:").find_next_sibling().get_text(strip=True))
        company = soup.find("span", string="Empresa:").find_next_sibling().get_text(strip=True)
        date = soup.find("span", string="Fecha de la transacción:").find_next_sibling().get_text(strip=True)
        cus = soup.find("span", string="CUS:").find_next_sibling().get_text(strip=True)

        payment_data.update({
            "value": value,
            "to": company,
            "date": date,
            "cus": cus,
            "state": APPROVED_STATE
        })
    except AttributeError as e:
        logger.warning(f"AttributeError in get_payment_data: {e}")
    except Exception as e:
        logger.error(f"Error in get_payment_data: {e}")
    return payment_data

def get_payment_from_nequi(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from Nequi HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    payment_data = {key: None for key in PAYMENT_KEYS}
    try:
        value = _safe_int(soup.find("td", string=re.compile(r"Valor:\s*\$?\s*\d")).get_text(strip=True).split(":")[-1])
        to = " ".join(soup.find("td", string=re.compile(r"Listo tu pago en")).get_text(strip=True).split(" ")[-2:])
        date = soup.find("td", string=re.compile(r"Fecha del pago")).get_text(strip=True).split(":")[-1].replace(" ", "")
        cus = soup.find("span", style=re.compile(r"color:#da0081")).get_text()
        state_text = soup.find("td", string=re.compile(r"Estado:")).get_text(strip=True).split(":")[-1].strip()
        state = APPROVED_STATE if state_text.lower() == "exito" else None

        payment_data.update({
            "value": value,
            "to": to,
            "date": date,
            "cus": cus,
            "state": state
        })
    except AttributeError as e:
        logger.warning(f"AttributeError in get_payment_from_nequi: {e}")
    except Exception as e:
        logger.error(f"Error in get_payment_from_nequi: {e}")
    return payment_data

def get_payment_from_puntored(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    Extract payment data from Puntored HTML.

    Args:
        soup: Parsed HTML content.

    Returns:
        Dict with payment data.
    """
    payment_data = {key: None for key in PAYMENT_KEYS}
    try:
        tag_divs = soup.find_all("div", class_="m_-5752786190590538227summary-info__item")

        for tag in tag_divs:
            label_span = tag.find("span", class_="m_-5752786190590538227label")
            if not label_span:
                continue
            label_text = label_span.get_text(strip=True)
            value_span = tag.find("span", class_="m_-5752786190590538227value")
            if not value_span:
                continue
            value_text = value_span.get_text(strip=True)

            if "Valor" in label_text:
                payment_data['value'] = _safe_int(value_text)
            elif label_text == "Convenio":
                payment_data['to'] = value_text
            elif label_text == "Fecha":
                payment_data['date'] = value_text
            elif label_text == "Aprobación":
                payment_data['cus'] = value_text

        state_tag = soup.find("h2", class_="m_-5752786190590538227summary-header__title")
        state = state_tag.get_text(strip=True) if state_tag else UNKNOWN_STATE
        payment_data['state'] = APPROVED_STATE if "exitosa" in state.lower() else state
    except Exception as e:
        logger.error(f"Error in get_payment_from_puntored: {e}")
    return payment_data

def get_payment(id: str, subject: str, html_payment: str, to: str) -> Dict[str, Any]:
    """
    Extract payment information from email HTML content.

    Args:
        id: Unique payment identifier.
        subject: Email subject.
        html_payment: HTML content of the email.
        to: Recipient information.

    Returns:
        Dict with extracted payment data.
    """
    soup = BeautifulSoup(html_payment, "html.parser")

    payment_data = {
        'id': id,
        'subject': subject,
        'state': "Aprovado"
    }

    # List of extractors in order of preference
    extractors = [
        lambda: extract_payment_values(soup),
        lambda: get_payment_data(soup),
        lambda: extract_from_notification(soup),
        lambda: get_payment_from_nequi(soup),
        lambda: get_payment_from_puntored(soup),
        lambda: extract_payment_gas(soup),
        lambda: extract_claro_invoice(soup),
        lambda: extract_enel_invoice(soup),
        lambda: extract_invoice_vue(soup, to),
        lambda: extract_from_alkosto(soup, to),
    ]

    for extractor in extractors:
        try:
            update = extractor()
        
            for key, value in update.items():
                if value is not None and payment_data.get(key) is None:
                    payment_data[key] = value
            if all(payment_data.get(key) is not None for key in PAYMENT_KEYS if key != 'state'):
                break
        except Exception as e:
            logger.error(f"Error in extractor: {e}")

    return payment_data

def get_html_payment(id: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """
    Fetch and parse HTML payment data from Microsoft Graph API.

    Args:
        id: Message ID.
        headers: Request headers.

    Returns:
        Dict with payment data or empty dict on failure.
    """
    path = f"https://graph.microsoft.com/v1.0/me/messages/{id}"
    try:
        response = requests.get(path, headers=headers)
        response.raise_for_status()
        data = response.json()
        html_payment = data.get('body', {}).get('content', '')
        subject = data.get('subject', 'No Subject')
        sender_name = data.get('sender', {}).get('emailAddress', {}).get('name', '')

        if 'rechazado' in subject.lower() or 'rechazada' in subject.lower():
            return {}

        payment_data = get_payment(id, subject, html_payment, sender_name)

        # Ensure state is approved if not set
        if not payment_data.get('state'):
            payment_data['state'] = APPROVED_STATE

        return payment_data
    except requests.RequestException as e:
        logger.error(f"Request error in get_html_payment: {e}")
    except Exception as e:
        logger.error(f"Error in get_html_payment: {e}")
    return {}
