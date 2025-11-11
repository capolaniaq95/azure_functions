import azure.functions as func
import logging
from typing import Dict, List, Optional, Any, Union
import zipfile
import xmltodict
from PyPDF2 import PdfReader
import fitz
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INVOICE_INFO = {"Total", "Date"}

# Constants
ENEL_MESSAGE_TEXT = "¡Con la factura virtual tienes toda la información al alcance de tu mano!"
GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0/me/messages"

class InvoiceData:
    def __init__(self, period: str, products: List[Dict[str, Any]], discount: float, total: float):
        self.period = period
        self.products = products
        self.discount = discount
        self.total = total

    def to_dict(self) -> Dict[str, Any]:
        return {
            'period': self.period,
            'products': self.products,
            'discount': self.discount,
            'total': self.total
        }

def extract_enel_invoice(email_id: str, headers: Dict[str, str]) -> Optional[Dict[str, str]]:
    """
    Extracts invoice data from Enel email content via Microsoft Graph API.

    Args:
        email_id: The ID of the email.
        headers: Authorization headers for the API request.

    Returns:
        A dictionary with extracted values or None if extraction fails.
    """
    logger.info(f"Extracting Enel invoice from email ID: {email_id}")

    url = f"{GRAPH_API_BASE_URL}/{email_id}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        data = response.json()
        html_content = data.get('body', {}).get('content', '')
        subject = data.get('subject', 'No Subject')

        soup = BeautifulSoup(html_content, 'html.parser')
        values = {'subject': subject, 'id': email_id}

        # Find the specific span and extract total and date
        target_span = soup.find('span', string=lambda x: x and ENEL_MESSAGE_TEXT in x)
        if target_span:
            total_span = target_span.find_next("span")
            if total_span:
                values['total'] = total_span.text.strip()
                date_span = total_span.find_next("span")
                if date_span:
                    values['date'] = date_span.text.strip()

        return values

    except requests.RequestException as e:
        logger.error(f"Failed to fetch email {email_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing email {email_id}: {e}")
        return None

def _extract_product_info(product: Dict[str, Any]) -> Dict[str, Any]:
    """Helper function to extract product information from XML product dict."""

    logging.info("Extracting product information from XML")
    product_dict = {}

    # Extract product ID
    product_id = product.get('cbc:ID')
    if isinstance(product_id, dict):
        product_id = product_id.get('#text')
    try:
        product_dict['product_id'] = int(product_id)
    except (ValueError, TypeError):
        raise ValueError("Invalid product ID in XML")

    # Extract other fields
    try:
        product_dict['quantity'] = float(product['cbc:InvoicedQuantity']['#text'])
        product_dict['subtotal'] = float(product['cbc:LineExtensionAmount']['#text'])
        product_dict['description'] = product['cac:Item']['cbc:Description']
        product_dict['price'] = float(product['cac:Price']['cbc:PriceAmount']['#text'])
    except KeyError as e:
        raise ValueError(f"Missing required field in product: {e}")

    # Extract taxes
    product_dict['taxes'] = 0.0
    tax_total = product.get('cac:TaxTotal')
    if tax_total:
        if isinstance(tax_total, list):
            for tax in tax_total:
                try:
                    product_dict['taxes'] += float(tax['cac:TaxSubtotal']['cbc:TaxAmount']['#text'])
                except (KeyError, ValueError):
                    pass
        else:
            try:
                product_dict['taxes'] = float(tax_total['cac:TaxSubtotal']['cbc:TaxAmount']['#text'])
            except (KeyError, ValueError):
                pass

    return product_dict

def get_from_xml(xml_data: Dict[str, Any]) -> InvoiceData:
    """
    Parses invoice information from XML data.

    Args:
        xml_data: Parsed XML data as a dictionary.

    Returns:
        InvoiceData object with extracted information.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    logging.info("Extracting invoice data from XML")
    try:
        invoice = xml_data['Invoice']
        period = invoice['ext:UBLExtensions']['ext:UBLExtension'][0]['ext:ExtensionContent']['sts:DianExtensions']['sts:InvoiceControl']['sts:AuthorizationPeriod']

        products = []
        invoice_lines = invoice['cac:InvoiceLine']
        if isinstance(invoice_lines, list):
            for product in invoice_lines:
                products.append(_extract_product_info(product))
        elif isinstance(invoice_lines, dict):
            products.append(_extract_product_info(invoice_lines))
        else:
            raise ValueError("Invalid invoice lines structure")

        legal_monetary = invoice['cac:LegalMonetaryTotal']
        discount = float(legal_monetary['cbc:AllowanceTotalAmount']['#text'])
        total = float(legal_monetary['cbc:PayableAmount']['#text'])

        return InvoiceData(period, products, discount, total).to_dict()
    
    except KeyError as e:
        raise ValueError(f"Missing required XML field: {e}")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid data in XML: {e}")

def get_from_attachment(path_attachment: Optional[str] = None, path_xml: Optional[str] = None) -> InvoiceData:
    """
    Extracts invoice data from attachment or XML file.

    Args:
        path_attachment: Path to the zip attachment file.
        path_xml: Path to the XML file.

    Returns:
        InvoiceData object.

    Raises:
        ValueError: If file processing fails.
    """
    logging.info("Extracting invoice data from attachment or XML file")

    if path_attachment:
        try:
            with zipfile.ZipFile(path_attachment, 'r') as zip_file:
                files = zip_file.namelist()
                xml_file = next((f for f in files if f.endswith('.xml')), None)
                if not xml_file:
                    raise ValueError("No XML file found in zip attachment")

                with zip_file.open(xml_file) as file:
                    data = xmltodict.parse(file.read())
                    xml_content = data['AttachedDocument']['cac:Attachment']['cac:ExternalReference']['cbc:Description']
                    return get_from_xml(xmltodict.parse(xml_content))

        except zipfile.BadZipFile:
            raise ValueError("Invalid zip file")
        except Exception as e:
            raise ValueError(f"Error processing attachment: {e}")

    elif path_xml:
        try:
            with open(path_xml, 'r', encoding='utf-8') as file:
                data = xmltodict.parse(file.read())
                return get_from_xml(data)
        except FileNotFoundError:
            raise ValueError(f"XML file not found: {path_xml}")
        except Exception as e:
            raise ValueError(f"Error processing XML file: {e}")
    else:
        raise ValueError("Either path_attachment or path_xml must be provided")


def _extract_alkosto_invoice(pdf_path: str, password: str) -> Dict[str, str]:
    """
    Extracts invoice information from an Alkosto PDF file.
    Args:
        pdf_path: Path to the PDF file.
        password: Password for encrypted PDFs.
    Returns:
        A dictionary with extracted invoice information.
    """

    logging.info(f"Extracting Alkosto invoice from PDF: {pdf_path}")

    reader = PdfReader(pdf_path)
    info = {}
    if reader.is_encrypted:
        if not password:
            raise ValueError("Password required for encrypted PDF")
        if not reader.decrypt(password):
            raise ValueError("Invalid password for PDF")

    all_text = ""
    for page in reader.pages:
        all_text += page.extract_text() + "\n"

    tip = 0
    for line in all_text.split('\n'):
        line = line.strip()
        if "REDEBAN" in line:
            clean_line = line.split("$")
            info['value'] = clean_line[-1].strip()
        elif "ELECTRONICA" in line and tip == 0:
            tip += 1
        elif tip == 1 and len(line.split()) == 0:
            tip +=1
        elif tip == 2:
            clean_line = line.split(" ")
            info['Date'] = clean_line[0].strip()
            tip = 0
    return info

def _extract_english_pdf(pdf_path, password):
    reader = PdfReader(pdf_path)
    info = {}
    if reader.is_encrypted:
        if not password:
            raise ValueError("Password required for encrypted PDF")
        if not reader.decrypt(password):
            raise ValueError("Invalid password for PDF")

    all_text = ""
    for page in reader.pages:
        all_text += page.extract_text() + "\n"

    tip = 1
    for line in all_text.split('\n'):
        cleaned_line = line.replace(' ', '').lower()
        if "total" in cleaned_line:
            if '$' in line:
                info['value'] = line.split("$")[-1].replace(" ", "")
        elif 'due' in line.replace(" ", "").lower() and tip == 1:
            clean_line = line.replace(" ", "").split("due")
            if clean_line[0] == 'Date':
                info['Date'] = clean_line[-1].replace(":", '')
                tipo = 0

    if len(info) == 2:
        return info
    else:
        return {}

def _extract_with_pypdf2(pdf_path, password):
    """
    Function to extract invoice information from a PDF using PyPDF2.
    Args:
        pdf_path: Path to the PDF file.
        password: Password for encrypted PDFs.
    Returns:
        A dictionary with extracted invoice information.
    """

    logging.info(f"Extracting invoice from PDF using PyPDF2: {pdf_path}")

    info = {}
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        if not password:
            raise ValueError("Password required for encrypted PDF")
        if not reader.decrypt(password):
            raise ValueError("Invalid password for PDF")

    all_text = ""
    for page in reader.pages:
        all_text += page.extract_text() + "\n"

    for line in all_text.split('\n'):
        line = line.strip()
        if "total" in line.lower():
            clean_line = line.split(' ')
            value = clean_line[-1].replace('$', '').replace(',', '')
            try:
                value = float(value)
                info['Total'] = clean_line[-1]
            except ValueError:
                value = None
        elif "date" in line.lower() or "fecha" in line.lower():
            clean_line = line.split(' ')
            value = clean_line[-1].replace(":", '')
            if any(val in value for val in ["/", '-']):
                info['Date'] = value
        if len(info) == 2:
            return info
    return {}

def _extract_with_fitz(pdf_path, password):
    logging.info(f"Extracting invoice from PDF using fitz: {pdf_path}")

    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        if not password:
            raise ValueError("Password required for encrypted PDF")
        if not doc.authenticate(password):
            raise ValueError("Invalid password for PDF")

    all_text = ""
    for page in doc:
        all_text += page.get_text() + "\n"

    for line in all_text.split('\n'):
        cleaned_line = line.replace(' ', '').lower()
        if "total" in cleaned_line and '$' in line:
            value = line.split("$")[-1].strip()
            return {"Total": value}

        if "date" in cleaned_line:
            date = line.split()[-1].strip()
            return {"Date": date}

    return {}


def extract_invoice_from_pdf(pdf_path: str, password: Optional[str] = None) -> Optional[List[str]]:
    """
    Extracts invoice information from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        password: Optional password for encrypted PDFs.

    Returns:
        List of strings containing total information, or None if not found.
    """
    info = {}

    extractors = [
        lambda: _extract_with_fitz(pdf_path, password),
        lambda: _extract_with_pypdf2(pdf_path, password),
        lambda: _extract_alkosto_invoice(pdf_path, password),
        lambda: _extract_english_pdf(pdf_path, password)
    ]

    for extractor in extractors:
        try:
            update = extractor()

            for key, value in update.items():
                if value is not None and info.get(key) is None:
                    info[key] = value
            if all(info.get(key) is not None for key in INVOICE_INFO if key != 'state'):
                break
        except Exception as e:
            logger.error(f"Error in extractor: {e}")

    return info